#!/usr/bin/env python3
"""
Stage 1b - discover provenance signals by scanning cloned repositories directly.

GitHub's search API caps every query at 1000 results and requires a token. A
local scan over a sampled repository corpus has neither limit, and for the
commit-trailer and SO-URL signals it has strictly higher recall: `git log --grep`
sees every commit in the history, whereas commit search sees the first thousand
across all of GitHub.

The trade-off is that the corpus must be sampled some other way (stage 1 with a
token, a curated list, or GH Archive). Given a corpus, this is the better
extractor, so it is what the full run should use.

Usage:
    python -m pipeline.p1b_local_scan --clone apache/commons-lang square/okhttp
    python -m pipeline.p1b_local_scan --scan
"""
from __future__ import annotations
import argparse, json, re, shutil, subprocess, sys
from pathlib import Path
from .config import INTERIM, REPOS
from . import store

SO_URL = re.compile(r"https?://(?:[a-z]+\.)?stackoverflow\.com/(?:a|questions)/\d+")

# Discovery deletes repositories with no signal, so "is it cloned?" cannot tell
# us whether we have already looked at one. A ledger can, which makes a
# corpus-wide pass resumable instead of re-cloning every discard.
LEDGER = INTERIM / "discovery_log.jsonl"


def ledger_seen() -> dict[str, str]:
    if not LEDGER.exists():
        return {}
    out = {}
    for line in LEDGER.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["repo"]] = d["verdict"]
    return out


def ledger_write(repo: str, verdict: str, channels: dict | None = None):
    with LEDGER.open("a") as fh:
        fh.write(json.dumps({"repo": repo, "verdict": verdict,
                             "channels": channels or {}}) + "\n")

# Commit-message patterns per channel. These are matched against full history
# with `git log --grep`, which is case-insensitive here by design.
COMMIT_PATTERNS = {
    "ai_trailer_claude":  r"Co-Authored-By: Claude",
    "ai_trailer_copilot": r"Co-authored-by: Copilot",
    "ai_trailer_cursor":  r"Co-authored-by: Cursor",
    "ai_trailer_devin":   r"Co-Authored-By: Devin",
    "ai_trailer_codex":   r"Co-authored-by: .*codex",
    "ai_message_chatgpt": r"chatgpt|gpt-4|gpt4",
    "ai_message_copilot": r"copilot",
    "so_commit_url":      r"stackoverflow\.com/(a|questions)/[0-9]+",
}


def run(cmd, cwd=None, check=False, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} -> {p.returncode}: {p.stderr[:300]}")
    return p.stdout


def clone_full(full_name: str, blobless: bool = False) -> Path | None:
    """Clone with full commit history. Shallow clones cannot support survival
    analysis, so `--depth` is never an option here.

    `blobless` adds `--filter=blob:none`, which fetches every commit but no file
    contents. That is all `git log --grep` needs, and it cuts a corpus-wide
    discovery pass from tens of gigabytes to a fraction of that. Blobs are
    fetched on demand later, so the same clone can be promoted to a full one
    with `git fetch` if the repository turns out to have hits.
    """
    dest = REPOS / full_name.replace("/", "__")
    if (dest / ".git").exists():
        return dest
    cmd = ["git", "clone", "--quiet", "--single-branch"]
    if blobless:
        cmd += ["--filter=blob:none", "--no-checkout"]
    cmd += [f"https://github.com/{full_name}.git", str(dest)]
    try:
        run(cmd, check=True, timeout=2400)
    except Exception as e:
        print(f"  ! {full_name}: {str(e)[:120]}")
        shutil.rmtree(dest, ignore_errors=True)
        return None
    return dest


def scan_commits(repo: Path, full_name: str) -> list[dict]:
    out = []
    for channel, pattern in COMMIT_PATTERNS.items():
        log = run(["git", "log", "--all", "-i", f"--grep={pattern}", "--extended-regexp",
                   "--format=%H%x1f%aI%x1f%s%x1e%b%x1d"], cwd=repo)
        for entry in log.split("\x1d"):
            entry = entry.strip()
            if not entry:
                continue
            head, _, body = entry.partition("\x1e")
            parts = head.split("\x1f")
            if len(parts) < 3:
                continue
            sha, date, subject = parts[0], parts[1], parts[2]
            if len(sha) != 40:
                continue
            out.append({"channel": channel, "repo": full_name, "sha": sha,
                        "commit_date": date,
                        "message": (subject + "\n" + body)[:1000]})
    return out


def scan_files(repo: Path, full_name: str) -> list[dict]:
    """.java files in the current checkout citing a Stack Overflow answer.

    Returns nothing for a blobless clone with no working tree - `git grep`
    would have to fetch every blob, defeating the point of the filter.
    """
    if not any(repo.iterdir()) or not (repo / ".git").exists():
        return []
    out = []
    hits = run(["git", "grep", "-l", "-E", SO_URL.pattern, "--", "*.java"], cwd=repo)
    for path in hits.splitlines():
        path = path.strip()
        if path:
            out.append({"channel": "so_code_url", "repo": full_name, "path": path})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clone", nargs="*", help="owner/name repositories to clone")
    ap.add_argument("--scan", action="store_true", help="scan everything already cloned")
    ap.add_argument("--corpus", action="store_true",
                    help="clone and scan the corpus sampled by p0_sample_corpus")
    ap.add_argument("--limit", type=int, default=0,
                    help="with --corpus, stop after this many repositories")
    ap.add_argument("--blobless", action="store_true",
                    help="discovery mode: clone commit history without file "
                         "contents, and delete repositories with no hits")
    a = ap.parse_args()

    per_channel_early: dict[str, list[dict]] = {}
    targets = list(a.clone or [])
    if a.corpus:
        f = INTERIM / "java_corpus.jsonl"
        if not f.exists():
            sys.exit("no java_corpus.jsonl - run p0_sample_corpus first")
        corpus = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        if a.limit:
            corpus = corpus[: a.limit]
        print(f"corpus: {len(corpus)} Java repositories")
        targets.extend(r["repo"] for r in corpus)

    repos: list[tuple[Path, str]] = []
    discarded = 0
    seen = ledger_seen() if a.blobless else {}
    if seen:
        before = len(targets)
        targets = [t for t in targets if t not in seen]
        print(f"ledger: skipping {before - len(targets)} already-scanned "
              f"repositories, {len(targets)} to go")
    for i, full in enumerate(targets, 1):
        p = clone_full(full, blobless=a.blobless)
        if not p:
            if a.blobless:
                ledger_write(full, "clone-failed")
            continue
        if a.blobless:
            # Scan immediately and keep only what has a hit, so the corpus-wide
            # pass never holds more than one unproductive clone at a time.
            hits = scan_commits(p, full)
            if hits:
                counts = {}
                for c in hits:
                    per_channel_early.setdefault(c["channel"], []).append(c)
                    counts[c["channel"]] = counts.get(c["channel"], 0) + 1
                # Persist per repository, not at the end. A corpus-wide pass is
                # hours long; losing all of it to one failure near the end is
                # not an acceptable failure mode.
                for channel, rows in counts.items():
                    store.merge(channel, [c for c in hits
                                          if c["channel"] == channel])
                ledger_write(full, "keep", counts)
                print(f"  [{i}/{len(targets)}] {full}: {counts}  KEEP")
            else:
                shutil.rmtree(p, ignore_errors=True)
                ledger_write(full, "discard")
                discarded += 1
                if i % 20 == 0:
                    print(f"  [{i}/{len(targets)}] scanned, {discarded} discarded")
            continue
        repos.append((p, full))
    if a.scan or not targets:
        for d in sorted(REPOS.iterdir()):
            if (d / ".git").exists() and not d.name.startswith("host__"):
                full = d.name.replace("__", "/", 1)
                if (d, full) not in repos:
                    repos.append((d, full))

    # Already persisted per repository in blobless mode; store.merge is
    # idempotent, so re-merging here is harmless and keeps one code path.
    per_channel: dict[str, list[dict]] = dict(per_channel_early)
    for repo, full in repos:
        cands = scan_commits(repo, full) + scan_files(repo, full)
        for c in cands:
            per_channel.setdefault(c["channel"], []).append(c)
        if cands:
            counts: dict[str, int] = {}
            for c in cands:
                counts[c["channel"]] = counts.get(c["channel"], 0) + 1
            print(f"  {full}: {counts}")
        else:
            print(f"  {full}: nothing")

    for channel, rows in per_channel.items():
        new, total = store.merge(channel, rows)
        print(f"{channel:22s} +{new} new ({len(rows)} found, {total} in channel)")

    if a.blobless:
        kept = len({c["repo"] for rows in per_channel_early.values() for c in rows})
        print(f"\ndiscovery pass: {len(targets)} repositories scanned, "
              f"{kept} kept, {discarded} discarded with no provenance signal")
        print("Promote the keepers to full clones before p3_clone_locate:")
        print("  git -C repos/<name> fetch --refetch origin && "
              "git -C repos/<name> checkout <default-branch>")
    else:
        print(f"\nscanned {len(repos)} repositories")


if __name__ == "__main__":
    sys.exit(main())
