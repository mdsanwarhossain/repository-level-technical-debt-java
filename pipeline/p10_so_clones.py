#!/usr/bin/env python3
"""
Stage 10 - rebuild the Stack Overflow arm by clone detection.

The URL channels failed empirically: 9 Java repositories with full working trees
produced zero `so_code_url` hits, and `so_commit_url` yielded one eligible
event. Developers copy Stack Overflow code without citing it, which is the whole
problem - so the only way to find it is to look for the code itself.

The query set is already in hand. The snippet study collected 500 Stack Overflow
Java answers with their question and answer ids, which is exactly the
"introduction vector" corpus the protocol describes: what is on offer to be
copied. This stage searches the repository corpus for it.

Method - inverted k-gram index, exact containment:

  1. Normalise: strip comments and string literals, fold whitespace, lowercase.
     Identifier names are kept, because renaming them is what separates a type-2
     clone from a coincidental structural match, and keeping them raises
     precision at some cost in recall.
  2. Shingle each snippet into token k-grams (default k=5) and build one
     inverted index: k-gram hash -> the snippets containing it.
  3. For each repository file, hash its k-grams and look each one up. That
     counts, in a single pass, how many of every snippet's k-grams the file
     contains.
  4. Score by **containment** - the fraction of the *snippet's* k-grams found in
     the file - not by Jaccard. A 10-line snippet pasted into a 500-line file
     has near-zero Jaccard but containment near 1, and containment is the right
     measure for "was this copied in".

The inverted index is what makes this tractable: comparing every file against
every snippet pairwise is O(files x snippets) and takes hours in Python, whereas
one index lookup per file k-gram is linear in corpus size. MinHash would be the
right tool for a query set of millions; for 500 queries it is pure overhead and
only approximates the number we can compute exactly.

Everything is stdlib - no NiCad or SourcererCC install, which keeps the pipeline
reproducible by a reviewer.

Precision here is the paper's weak point and must be validated by hand
(`p7_validate`). Treat every match as a candidate, never as a fact.

Usage:
    python -m pipeline.p10_so_clones --min-containment 0.6
    python -m pipeline.p10_so_clones --k 5 --min-containment 0.5
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, struct, sys
from collections import defaultdict
from pathlib import Path
from .config import INTERIM, REPOS
from . import store

SNIPPET_CSV = Path("/home/mdsanwarhossain/Downloads/paper software evolution journal"
                   "/KAje lagabo/dataset_clean.csv")

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
STRING_LIT = re.compile(r'"(?:\\.|[^"\\])*"' r"|'(?:\\.|[^'\\])*'")
TOKEN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d+|[^\s\w]")

MASK64 = (1 << 64) - 1
# Set from --min-grams before the snippets are loaded.
MIN_GRAMS = [30]

# Paths that are not authored code. The first run of this stage returned 103 of
# 107 matches from one repository's `decompiled/java/` tree: decompiled bytecode
# is boilerplate-dense and matches generic snippet k-grams readily, producing
# pure false positives. Generated sources have the same problem.
# `gen` and `out` are deliberately NOT matched bare: a legitimate package can be
# named `org/gen/`, and excluding it would lose authored code. Build output is
# caught by the specific directory names instead.
EXCLUDE_PATH = re.compile(
    r"(^|/)(decompiled|generated|build|target|node_modules|\.gradle|"
    r"vendor|third[_-]?party|src/gen|gen-src|generated-sources)(/|$)", re.I)
# Minimum vocabulary a snippet must have before a match means anything. A
# snippet of 15 k-grams is roughly 19 tokens - `list.add(x); for (int i = 0; ...`
# occurs in thousands of files and attributing it to one answer is noise.


def normalise(code: str) -> list[str]:
    code = BLOCK_COMMENT.sub(" ", code)
    code = LINE_COMMENT.sub(" ", code)
    code = STRING_LIT.sub(' "S" ', code)
    return [t.lower() for t in TOKEN.findall(code)]


def kgrams(tokens: list[str], k: int) -> set[int]:
    if len(tokens) < k:
        return set()
    out = set()
    for i in range(len(tokens) - k + 1):
        h = hashlib.blake2b(" ".join(tokens[i:i + k]).encode(),
                            digest_size=8).digest()
        out.add(struct.unpack("<Q", h)[0])
    return out


def containment(snippet: set[int], file_grams: set[int]) -> float:
    """Fraction of the snippet's k-grams present in the file."""
    if not snippet:
        return 0.0
    return len(snippet & file_grams) / len(snippet)


def load_snippets(k: int) -> list[dict]:
    if not SNIPPET_CSV.exists():
        sys.exit(f"snippet corpus not found at {SNIPPET_CSV}")
    out = []
    with SNIPPET_CSV.open(newline="") as fh:
        for r in csv.DictReader(fh):
            code = r.get("code_snippet_so") or ""
            toks = normalise(code)
            grams = kgrams(toks, k)
            if len(grams) < MIN_GRAMS[0]:
                continue        # too short to attribute with any confidence
            out.append({
                "so_question_id": r["so_question_id"],
                "so_answer_id": r["so_answer_id"],
                "n_tokens": len(toks),
                "grams": grams,
            })
    return out


def repo_dirs() -> list[tuple[Path, str]]:
    out = []
    for d in sorted(REPOS.iterdir()):
        if (d / ".git").exists() and not d.name.startswith("host__"):
            out.append((d, d.name.replace("__", "/", 1)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5, help="token k-gram size")
    ap.add_argument("--min-containment", type=float, default=0.8,
                    help="fraction of a snippet's k-grams the file must contain")
    ap.add_argument("--min-grams", type=int, default=30,
                    help="skip snippets with fewer k-grams than this - too "
                         "short to attribute with any confidence")
    ap.add_argument("--min-shared", type=int, default=25,
                    help="absolute floor on shared k-grams; containment alone "
                         "is meaningless for a tiny snippet")
    ap.add_argument("--allow-generated", action="store_true",
                    help="do not skip decompiled/generated source trees")
    ap.add_argument("--max-file-tokens", type=int, default=60000)
    a = ap.parse_args()

    MIN_GRAMS[0] = a.min_grams
    snippets = load_snippets(a.k)
    print(f"{len(snippets)} Stack Overflow Java answers usable as queries "
          f"(k={a.k})")

    # gram hash -> snippet indices containing it
    index: dict[int, list[int]] = defaultdict(list)
    for i, sn in enumerate(snippets):
        for g in sn["grams"]:
            index[g].append(i)
    print(f"inverted index: {len(index)} distinct k-grams")

    repos = repo_dirs()
    print(f"{len(repos)} repositories in the corpus\n")

    matches, files_scanned, skipped_paths = [], 0, 0
    for repo, full in repos:
        hits_here = 0
        for f in repo.rglob("*.java"):
            rel_path = f.relative_to(repo).as_posix()
            if not a.allow_generated and EXCLUDE_PATH.search(rel_path):
                skipped_paths += 1
                continue
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            toks = normalise(text)
            if len(toks) < a.k or len(toks) > a.max_file_tokens:
                continue
            grams = kgrams(toks, a.k)
            files_scanned += 1

            # One pass over the file's k-grams counts hits for every snippet
            # at once; only snippets that actually share a k-gram are scored.
            hits: dict[int, int] = defaultdict(int)
            for g in grams:
                for i in index.get(g, ()):
                    hits[i] += 1

            for i, n_shared in hits.items():
                sn = snippets[i]
                if n_shared < a.min_shared:
                    continue
                cont = n_shared / len(sn["grams"])
                if cont < a.min_containment:
                    continue
                rel = rel_path
                matches.append({
                    "channel": "so_clone",
                    "repo": full,
                    "path": rel,
                    "so_question_id": sn["so_question_id"],
                    "so_answer_id": sn["so_answer_id"],
                    "containment": round(cont, 3),
                    "shared_kgrams": n_shared,
                    "snippet_kgrams": len(sn["grams"]),
                    "snippet_tokens": sn["n_tokens"],
                    "file_tokens": len(toks),
                    "evidence": f"https://stackoverflow.com/a/{sn['so_answer_id']}",
                })
                hits_here += 1
        if hits_here:
            print(f"  {full}: {hits_here} candidate clone(s)")

    if matches:
        new, total = store.merge("so_clone", matches)
        print(f"\n{len(matches)} candidate matches, +{new} new, {total} in channel")
    else:
        print("\nno candidate clones above the thresholds")
    print(f"{files_scanned} Java files scanned, "
          f"{skipped_paths} skipped as generated or decompiled")

    summary = {
        "k": a.k, "min_containment": a.min_containment,
        "min_grams": a.min_grams,
        "min_shared": a.min_shared,
        "snippets": len(snippets), "repos": len(repos),
        "files_scanned": files_scanned, "skipped_paths": skipped_paths,
        "matches": len(matches),
    }
    (INTERIM / "so_clone_summary.json").write_text(json.dumps(summary, indent=2))
    print("\nEvery match is a CANDIDATE. Run p7_validate before using any of "
          "them - clone detection precision is this arm's weak point, and "
          "reviewers will test it first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
