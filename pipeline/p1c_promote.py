#!/usr/bin/env python3
"""
Stage 1c - promote blobless discovery clones to full clones.

`p1b_local_scan --blobless` fetches commit history without file contents, which
is all `git log --grep` needs and is the only way a corpus-wide discovery pass
fits on disk. Everything after discovery - diffing the introduction commit,
`git blame --reverse`, running an analyser over a checkout - needs the blobs.

This stage fetches them for the repositories that actually had a hit, and
checks out a working tree. It is idempotent: a repository that is already a
full clone is left alone.

Usage:
    python -m pipeline.p1c_promote            # every repo with candidates
    python -m pipeline.p1c_promote --dry-run
"""
from __future__ import annotations
import argparse, json, shutil, subprocess, sys
from pathlib import Path
from .config import INTERIM, REPOS
from . import store


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def run(cmd, cwd=None, check=False, timeout=2400):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} -> {p.returncode}: {p.stderr[-250:]}")
    return p.stdout


def is_blobless(repo: Path) -> bool:
    cfg = run(["git", "config", "--get-all", "remote.origin.promisor"], cwd=repo)
    filt = run(["git", "config", "--get-all", "remote.origin.partialclonefilter"],
               cwd=repo)
    return bool(cfg.strip() or filt.strip())


def promote(repo: Path) -> str:
    """Fetch the blobs and lay down a working tree. Returns a status word."""
    if not (repo / ".git").exists():
        return "not-a-clone"
    if not is_blobless(repo) and run(["git", "ls-files"], cwd=repo).strip():
        return "already-full"

    # Drop the partial-clone filter, then refetch so every blob arrives.
    run(["git", "config", "--unset-all", "remote.origin.partialclonefilter"],
        cwd=repo)
    run(["git", "config", "--unset-all", "remote.origin.promisor"], cwd=repo)
    try:
        run(["git", "fetch", "--refetch", "--quiet", "origin"], cwd=repo, check=True)
    except Exception as e:
        return f"fetch-failed: {str(e)[:80]}"

    branch = (run(["git", "symbolic-ref", "--short", "HEAD"], cwd=repo).strip()
              or run(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                     cwd=repo).strip().removeprefix("origin/")
              or "HEAD")
    try:
        run(["git", "checkout", "--quiet", "--force", branch], cwd=repo, check=True)
    except Exception:
        try:
            run(["git", "checkout", "--quiet", "--force", "FETCH_HEAD"],
                cwd=repo, check=True)
        except Exception as e:
            return f"checkout-failed: {str(e)[:80]}"
    return "promoted"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=15.0,
                    help="stop promoting when free disk falls below this. "
                         "Full-history clones of large projects run to "
                         "gigabytes each, and filling the disk mid-pass would "
                         "corrupt clones rather than just stop the run.")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    # Which channels each repository contributes to, so promotion can be
    # ordered by research value rather than alphabetically. Full-history clones
    # of large projects run to gigabytes, so if disk runs out the repositories
    # we most need must already be on it.
    arms: dict[str, set[str]] = {}
    for f in sorted(INTERIM.glob("candidates_*.jsonl")):
        channel = f.stem.removeprefix("candidates_")
        for row in store.load(channel):
            if row.get("java_eligible") is False:
                continue
            arms.setdefault(row["repo"], set()).add(channel)
    if not arms:
        sys.exit("no candidates found - run discovery first")

    def rank(repo: str) -> tuple[int, str]:
        ch = arms[repo]
        has_ai = any(c.startswith("ai_trailer") for c in ch)
        has_so = any(c.startswith("so_") for c in ch)
        if has_ai and has_so:
            return (0, repo)      # dual-arm: supports the within-repo AI vs SO
        if has_so:
            return (1, repo)      # SO is the scarce arm
        if has_ai:
            return (2, repo)
        return (3, repo)

    wanted = set(arms)
    order = sorted(wanted, key=rank)
    tiers = {0: "dual-arm", 1: "SO-only", 2: "AI-only", 3: "other"}
    by_tier: dict[int, int] = {}
    for r in order:
        by_tier[rank(r)[0]] = by_tier.get(rank(r)[0], 0) + 1
    print("promotion order: "
          + ", ".join(f"{tiers[k]}={v}" for k, v in sorted(by_tier.items())))
    if a.limit:
        order = order[: a.limit]
    print(f"{len(wanted)} repositories carry candidates"
          + (f", promoting {len(order)}" if a.limit else "")
          + f"; {free_gb(REPOS):.1f} GB free")
    counts: dict[str, int] = {}
    for i, full in enumerate(order, 1):
        if not a.dry_run and free_gb(REPOS) < a.min_free_gb:
            print(f"\n!! stopping: only {free_gb(REPOS):.1f} GB free "
                  f"(--min-free-gb {a.min_free_gb}). "
                  f"{len(order) - i + 1} repositories left unpromoted; "
                  f"promotion is idempotent, so free space and re-run.")
            counts["stopped-low-disk"] = len(order) - i + 1
            break
        repo = REPOS / full.replace("/", "__")
        if not (repo / ".git").exists():
            counts["missing-clone"] = counts.get("missing-clone", 0) + 1
            continue
        if a.dry_run:
            state = "blobless" if is_blobless(repo) else "full"
            print(f"  [{i}/{len(order)}] {full}: {state}")
            counts[state] = counts.get(state, 0) + 1
            continue
        status = promote(repo)
        counts[status] = counts.get(status, 0) + 1
        print(f"  [{i}/{len(order)}] {full}: {status} "
              f"({free_gb(REPOS):.1f} GB free)")

    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if not a.dry_run:
        print("Next: p3_clone_locate, then p4_measure.")


if __name__ == "__main__":
    sys.exit(main())
