#!/usr/bin/env python3
"""
Stage 2b - annotate candidates with repository metadata before cloning.

DevGPT records no repository language, so without this every one of its 697
candidates would need a full-history clone just to discover it is not Java.
One `/repos/{owner}/{name}` call per distinct repository answers language,
stars, fork/archived status and age at a cost of one core-rate-limit unit
(5000/hour) instead of a clone.

The metadata is also what the eligibility filter and the confounder adjustment
in p6 need, so it is worth collecting for every channel, not just DevGPT.

Repositories that have been deleted or made private since the dataset was built
are marked `gone` - that is survivorship bias, and the paper reports how much of
it there is rather than quietly dropping those rows.

Usage:
    python -m pipeline.p2b_enrich --channel ai_devgpt
    python -m pipeline.p2b_enrich                     # every candidates_*.jsonl
"""
from __future__ import annotations
import argparse, json, sys, time
from collections import Counter
from .config import INTERIM, MIN_STARS
from .github_api import GitHub

CACHE = INTERIM / "repo_metadata.json"


def load_cache() -> dict:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def save_cache(c: dict):
    CACHE.write_text(json.dumps(c, indent=0))


def lookup(gh: GitHub, full: str, cache: dict) -> dict:
    if full in cache:
        return cache[full]
    try:
        data, _ = gh.get(f"/repos/{full}")
        rec = {
            "language": data.get("language"),
            "stars": data.get("stargazers_count"),
            "fork": data.get("fork"),
            "archived": data.get("archived"),
            "created_at": data.get("created_at"),
            "pushed_at": data.get("pushed_at"),
            "size_kb": data.get("size"),
            "default_branch": data.get("default_branch"),
            "gone": False,
        }
    except Exception as e:
        msg = str(e)
        rec = {"gone": True, "error": msg[:120]}
    cache[full] = rec
    time.sleep(0.1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append")
    a = ap.parse_args()

    files = ([INTERIM / f"candidates_{c}.jsonl" for c in a.channel] if a.channel
             else sorted(INTERIM.glob("candidates_*.jsonl")))
    files = [f for f in files if f.exists()]
    if not files:
        sys.exit("no candidates_*.jsonl found")

    gh = GitHub()
    cache = load_cache()
    print(f"{len(cache)} repositories already cached")

    for f in files:
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        repos = sorted({r["repo"] for r in rows})
        print(f"\n[{f.stem.removeprefix('candidates_')}] "
              f"{len(rows)} candidates in {len(repos)} repositories")

        for i, full in enumerate(repos, 1):
            if full not in cache:
                lookup(gh, full, cache)
                if i % 25 == 0:
                    save_cache(cache)
                    print(f"    {i}/{len(repos)} looked up")
        save_cache(cache)

        langs, verdicts = Counter(), Counter()
        for r in rows:
            m = cache.get(r["repo"], {})
            r |= {k: m.get(k) for k in
                  ("language", "stars", "fork", "archived", "created_at",
                   "pushed_at", "default_branch", "gone")}
            langs[(m.get("language") or ("GONE" if m.get("gone") else "unknown"))] += 1
            r["java_eligible"] = bool(
                not m.get("gone") and not m.get("fork") and not m.get("archived")
                and (m.get("language") or "").lower() == "java"
                and (m.get("stars") or 0) >= MIN_STARS)
            verdicts["eligible" if r["java_eligible"] else "excluded"] += 1

        with f.open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

        top = ", ".join(f"{k}={v}" for k, v in langs.most_common(6))
        print(f"  languages: {top}")
        print(f"  java-eligible: {verdicts['eligible']} / {len(rows)}")
        print(f"  -> {f.name} (annotated in place)")

    gone = sum(1 for v in cache.values() if v.get("gone"))
    print(f"\n{len(cache)} repositories cached, {gone} gone (deleted or private) "
          f"-> {CACHE.name}")
    print("Survivorship bias: those rows are retained and flagged, not dropped.")


if __name__ == "__main__":
    sys.exit(main())
