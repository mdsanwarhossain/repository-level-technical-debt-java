#!/usr/bin/env python3
"""
Stage 0 - sample the Java repository corpus.

Measured 2026-10-28, and it inverts the mining strategy. The commit-trailer
signal is enormous:

    "Co-Authored-By: Claude"    22,360,758 commits
    "Co-authored-by: Cursor"     3,655,166
    "Co-authored-by: Copilot"    3,074,232
    "aider: "                      871,521
    "Co-Authored-By: Devin"        591,002

but **not one of 500 sampled commits came from a Java repository** - the results
are dominated by TypeScript, Python, Rust and C++. GitHub's commit search has no
`language:` qualifier, so there is no way to ask it for Java commits, and
sampling globally will essentially never surface them.

Repository search *does* support `language:`. So the corpus must be built the
other way round: sample Java repositories first, clone them, then scan their
full histories locally with p1b_local_scan. That is also the higher-recall path
- `git log --grep` sees every commit in a repository, whereas commit search
caps at 1000 results across all of GitHub.

Repository search is also capped at 1000 results per query, so the corpus is
built from several disjoint star bands rather than one query.

Usage:
    python -m pipeline.p0_sample_corpus --target 300
    python -m pipeline.p0_sample_corpus --target 300 --min-pushed 2024-01-01
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from .config import INTERIM, MIN_STARS
from .github_api import GitHub

# Disjoint star bands keep each query under the 1000-result ceiling and spread
# the corpus across project sizes instead of returning only the famous ones.
BANDS = ["10..30", "31..100", "101..500", "501..2000", "2001..10000", ">10000"]


def sample(gh: GitHub, target: int, min_pushed: str, bands: list[str]) -> list[dict]:
    per_band = max(target // len(bands), 10)
    seen: dict[str, dict] = {}
    for band in bands:
        q = f"language:java stars:{band} pushed:>{min_pushed} fork:false archived:false"
        print(f"\n[stars {band}] {q}")
        items = gh.search("repositories", q, cap=per_band)
        total = getattr(gh, "last_total", 0)
        print(f"  {total} repositories match, taking {len(items)}")
        for r in items:
            full = r["full_name"]
            if full in seen:
                continue
            seen[full] = {
                "repo": full,
                "stars": r.get("stargazers_count"),
                "language": r.get("language"),
                "created_at": r.get("created_at"),
                "pushed_at": r.get("pushed_at"),
                "size_kb": r.get("size"),
                "default_branch": r.get("default_branch"),
                "star_band": band,
            }
    return list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=300,
                    help="roughly how many repositories to sample in total")
    ap.add_argument("--min-pushed", default="2023-06-01",
                    help="only repositories pushed since this date; the AI "
                         "channels cannot predate 2023 so older repositories "
                         "contribute to the SO arm only")
    ap.add_argument("--band", action="append", help="override the star bands")
    a = ap.parse_args()

    gh = GitHub()
    rows = sample(gh, a.target, a.min_pushed, a.band or BANDS)
    out = INTERIM / "java_corpus.jsonl"
    with out.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    bands = Counter(r["star_band"] for r in rows)
    print(f"\n{len(rows)} Java repositories sampled")
    for b in (a.band or BANDS):
        print(f"  stars {b:12s} {bands.get(b, 0)}")
    print(f"-> {out}")
    print("\nNext: clone and scan these locally - commit search cannot filter "
          "by language, but `git log --grep` over a Java corpus can.")
    print("  ./env/bin/python -m pipeline.p1b_local_scan --corpus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
