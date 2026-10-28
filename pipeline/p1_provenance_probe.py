#!/usr/bin/env python3
"""
Stage 1 - provenance probe (the go/no-go gate).

Before scaling to hundreds of repositories we need to know, per channel, how
many *introduction events* exist and how precise the attribution is. An
introduction event is a commit where code traceable to an external source
enters a maintained Java repository.

Channels are deliberately kept separate: they have very different
precision/recall profiles and the paper must report them separately.

  AI channel
    ai_trailer_*   machine-written commit trailers. Highest precision AI
                   provenance that exists in the wild - the tool writes these,
                   not the developer, so they are not self-report noise.
    ai_message_*   developer-written commit messages naming a tool. Lower
                   precision: "revert ChatGPT change" is not an introduction.

  Stack Overflow channel
    so_code_url    answer URL in a .java file comment. Established high-precision
                   approach (Abdalkareem et al.).
    so_commit_url  answer URL in the commit message.

Usage:
    python -m pipeline.p1_provenance_probe            # probe all channels
    python -m pipeline.p1_provenance_probe --channel so_code_url --cap 200
"""
from __future__ import annotations
import argparse, json, sys, time
from collections import Counter
from .config import INTERIM, MIN_STARS
from .github_api import GitHub
from . import store

# kind, query. Commit search has no `language:` qualifier, so Java filtering
# happens downstream against the repository record.
CHANNELS = {
    "ai_trailer_claude":  ("commits", '"Co-Authored-By: Claude"'),
    "ai_trailer_copilot": ("commits", '"Co-authored-by: Copilot"'),
    "ai_trailer_cursor":  ("commits", '"Co-authored-by: Cursor"'),
    "ai_trailer_aider":   ("commits", '"aider: "'),
    "ai_trailer_devin":   ("commits", '"Co-Authored-By: Devin"'),
    "ai_message_chatgpt": ("commits", "ChatGPT"),
    "ai_message_copilot": ("commits", "Copilot generated"),
    "so_commit_url":      ("commits", "stackoverflow.com/a"),
    "so_code_url":        ("code", 'stackoverflow.com/a language:java'),
    "so_code_url_q":      ("code", 'stackoverflow.com/questions language:java'),
}


def repo_ok(gh: GitHub, full_name: str, cache: dict) -> tuple[bool, dict]:
    """Java, non-fork, enough stars to be plausibly maintained."""
    if full_name not in cache:
        try:
            data, _ = gh.get(f"/repos/{full_name}")
        except Exception as e:
            data = {"_error": str(e)}
        cache[full_name] = data
        time.sleep(0.4)
    r = cache[full_name]
    ok = (
        not r.get("_error")
        and not r.get("fork", True)
        and not r.get("archived", True)
        and (r.get("language") or "") .lower() == "java"
        and (r.get("stargazers_count") or 0) >= MIN_STARS
    )
    return ok, r


def probe(gh: GitHub, name: str, cap: int, repo_cache: dict) -> dict:
    kind, query = CHANNELS[name]
    print(f"\n[{name}] /search/{kind}  q={query}")
    items = gh.search(kind, query, cap=cap)
    total = getattr(gh, "last_total", 0)
    print(f"  GitHub reports total_count={total}, pulled {len(items)}")

    out, reasons = [], Counter()
    for it in items:
        repo = it.get("repository", {}) or {}
        full = repo.get("full_name")
        if not full:
            reasons["no_repo"] += 1
            continue
        ok, rec = repo_ok(gh, full, repo_cache)
        if not ok:
            reasons[
                "error" if rec.get("_error") else
                "fork" if rec.get("fork") else
                "archived" if rec.get("archived") else
                f"lang={rec.get('language')}" if (rec.get("language") or "").lower() != "java" else
                "few_stars"
            ] += 1
            continue
        row = {
            "channel": name,
            "repo": full,
            "stars": rec.get("stargazers_count"),
            "created_at": rec.get("created_at"),
            "pushed_at": rec.get("pushed_at"),
            "size_kb": rec.get("size"),
        }
        if kind == "commits":
            row |= {
                "sha": it.get("sha"),
                "commit_date": (it.get("commit", {}).get("author") or {}).get("date"),
                "message": (it.get("commit", {}).get("message") or "")[:1000],
            }
        else:
            row |= {"path": it.get("path"), "file_sha": it.get("sha")}
        out.append(row)

    n_new, n_channel = store.merge(name, out)
    print(f"  kept {len(out)} java candidates in {len(set(r['repo'] for r in out))} repos")
    print(f"  dropped: {dict(reasons)}")
    print(f"  merged: +{n_new} new, {n_channel} in channel -> "
          f"{store.path_for(name).name}")
    return {"channel": name, "kind": kind, "query": query,
            "github_total": total, "pulled": len(items), "kept": len(out),
            "in_channel": n_channel,
            "repos": len(set(r["repo"] for r in out)), "dropped": dict(reasons),
            "sampled_languages": dict(reasons)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append", choices=list(CHANNELS))
    ap.add_argument("--cap", type=int, default=300,
                    help="max search results per channel (GitHub ceiling is 1000)")
    a = ap.parse_args()

    gh = GitHub()
    rl = gh.rate_limit()
    print("rate limit:", {k: v["remaining"] for k, v in rl.items() if k in ("core", "search", "code_search")})

    repo_cache: dict = {}
    summary = [probe(gh, c, a.cap, repo_cache) for c in (a.channel or CHANNELS)]

    out = INTERIM / "probe_summary.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n{'channel':22s} {'total':>9s} {'kept':>6s} {'repos':>6s}")
    for s in summary:
        print(f"{s['channel']:22s} {s['github_total']:>9d} {s['kept']:>6d} {s['repos']:>6d}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    sys.exit(main())
