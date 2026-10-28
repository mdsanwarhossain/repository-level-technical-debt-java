#!/usr/bin/env python3
"""
Stage 5 - track each introduction event forward through history.

This is the stage that makes the study a software-evolution paper rather than a
static measurement.

Why `git blame --reverse` and not `git log -L`: `git log -L a,b:file` interprets
the line range in the revision it starts walking from, which is HEAD. The lines
we care about are at a..b in the *introduction* commit, and by HEAD they have
moved or gone. `git blame --reverse intro..HEAD -L a,b -- file` is anchored the
right way round: for every line as it existed at the introduction commit it
reports the last commit in which that line still existed. That is a survival
measurement by construction.

Per introduced line range we record:

  lines_surviving        still present at HEAD
  time_to_first_change   days from introduction until the first line was altered
  time_to_last_survival  days the longest-lived line lasted
  file_touches           later commits that modified the file (change-proneness)
  fix_touches            of those, how many look like bug fixes (SZZ-lite)
  observed_days          introduction -> HEAD, the exposure time
  duration_days /
  event_observed         survival-analysis coding, censored at the window

Censoring is the point. A range not yet disturbed inside the observation window
is *right-censored*, not a zero. Without it, AI-attributed code - which cannot
predate 2023 - would spuriously appear to be remediated faster purely because it
has been watched for less time. The pilot data shows exactly this asymmetry:
Stack Overflow events in this corpus have thousands of commits of exposure after
introduction, the AI events have tens.

Approximation, documented: with `--reverse`, blame reports the commit in which a
line was *last seen*, so the change itself happened in that commit's child. Times
are therefore accurate to one commit, which is far below the resolution of any
claim the paper makes.

Usage:
    python -m pipeline.p5_track
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from .config import INTERIM, REPOS, OBSERVATION_WINDOW_DAYS

FIX = re.compile(r"\b(fix(e[sd])?|bug|defect|patch|hotfix|resolve[sd]?|"
                 r"crash|regression|npe|exception|broken)\b", re.I)


def run(cmd, cwd=None, check=False, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} -> {p.returncode}: {p.stderr[:300]}")
    return p.stdout


def blame_reverse(repo: Path, path: str, a: int, b: int, intro_sha: str) -> list[dict]:
    """Per line in a..b at `intro_sha`, the last commit in which it existed."""
    out = run(["git", "blame", "--reverse", "--porcelain", "-L", f"{a},{b}",
               f"{intro_sha}..HEAD", "--", path], cwd=repo)
    lines, cur = [], None
    for raw in out.splitlines():
        m = re.match(r"^([0-9a-f]{40}) (\d+) (\d+)", raw)
        if m:
            cur = {"sha": m.group(1), "orig_line": int(m.group(2))}
            lines.append(cur)
        elif cur is not None and raw.startswith("committer-time "):
            cur["time"] = int(raw.split()[1])
        elif cur is not None and raw.startswith("summary "):
            cur["summary"] = raw[8:]
    return [l for l in lines if "time" in l]


def file_touches(repo: Path, path: str, intro_sha: str) -> list[dict]:
    """Commits after the introduction that modified the file."""
    out = run(["git", "log", f"{intro_sha}..HEAD", "--follow",
               "--format=%H%x1f%aI%x1f%ae%x1f%s", "--", path], cwd=repo)
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4 and len(parts[0]) == 40:
            rows.append({"sha": parts[0], "date": parts[1],
                         "email": parts[2], "subject": parts[3]})
    return rows


def track_event(repo: Path, ev: dict, head_sha: str, head_date: str) -> list[dict]:
    t_intro = datetime.fromisoformat(ev["intro_date"])
    t_head = datetime.fromisoformat(head_date)
    observed = max((t_head - t_intro).days, 0)
    window = min(observed, OBSERVATION_WINDOW_DAYS)

    rows = []
    for path, ranges in ev["files"].items():
        touches = file_touches(repo, path, ev["intro_sha"])
        for a, b in ranges:
            blamed = blame_reverse(repo, path, a, b, ev["intro_sha"])
            n_lines = b - a + 1
            if not blamed:
                continue

            surviving = [l for l in blamed if l["sha"] == head_sha]
            changed = [l for l in blamed if l["sha"] != head_sha]

            # Days from introduction until the first line of the range was
            # altered; None if every line is still at HEAD.
            if changed:
                first = min(l["time"] for l in changed)
                ttf = max((datetime.fromtimestamp(first, timezone.utc) - t_intro).days, 0)
            else:
                ttf = None
            last_survival = max(
                (datetime.fromtimestamp(l["time"], timezone.utc) - t_intro).days
                for l in blamed)

            if ttf is not None and ttf <= window:
                duration, event_observed = ttf, 1
            else:
                duration, event_observed = window, 0

            in_window = [t for t in touches
                         if (datetime.fromisoformat(t["date"]) - t_intro).days <= window]
            rows.append({
                "channel": ev["channel"],
                "source": ev["source"],
                "repo": ev["repo"],
                "stars": ev.get("stars"),
                "intro_sha": ev["intro_sha"],
                "intro_date": ev["intro_date"],
                "file": path,
                "is_test": "/test/" in path or path.endswith("Test.java"),
                "line_start": a,
                "line_end": b,
                "n_lines": n_lines,
                "lines_blamed": len(blamed),
                "lines_surviving": len(surviving),
                "frac_surviving": len(surviving) / len(blamed),
                "observed_days": observed,
                "window_days": window,
                "time_to_first_change": ttf,
                "time_to_last_survival": last_survival,
                "duration_days": duration,
                "event_observed": event_observed,
                "file_touches": len(in_window),
                "file_touches_all": len(touches),
                "n_authors": len({t["email"] for t in in_window}),
                "fix_touches": sum(1 for t in in_window if FIX.search(t["subject"])),
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    f = INTERIM / "introduction_events.jsonl"
    if not f.exists():
        sys.exit("no introduction_events.jsonl - run p3_clone_locate first")
    events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    events = [e for e in events if e["eligible"]]
    if a.limit:
        events = events[: a.limit]

    heads: dict[str, tuple[str, str]] = {}
    out_path = INTERIM / "survival_rows.jsonl"
    n = 0
    with out_path.open("w") as out:
        for i, ev in enumerate(events, 1):
            repo = REPOS / ev["repo"].replace("/", "__")
            if not (repo / ".git").exists():
                continue
            if ev["repo"] not in heads:
                run(["git", "checkout", "--quiet", "--force",
                     run(["git", "symbolic-ref", "--short", "HEAD"], cwd=repo).strip()
                     or "HEAD"], cwd=repo)
                heads[ev["repo"]] = (
                    run(["git", "rev-parse", "HEAD"], cwd=repo).strip(),
                    run(["git", "log", "-1", "--format=%aI", "HEAD"], cwd=repo).strip(),
                )
            head_sha, head_date = heads[ev["repo"]]
            try:
                rows = track_event(repo, ev, head_sha, head_date)
            except Exception as e:
                print(f"  ! {ev['repo']} {ev['intro_sha'][:8]}: {type(e).__name__}: {e}")
                continue
            for r in rows:
                out.write(json.dumps(r) + "\n")
            n += len(rows)
            surv = sum(r["lines_surviving"] for r in rows)
            tot = sum(r["lines_blamed"] for r in rows)
            print(f"[{i}/{len(events)}] {ev['repo']} {ev['intro_sha'][:8]} "
                  f"({ev['source']}) {len(rows)} ranges, "
                  f"{surv}/{tot} lines alive at HEAD, "
                  f"{sum(r['event_observed'] for r in rows)} changed in window, "
                  f"{rows[0]['observed_days'] if rows else 0}d exposure")
    print(f"\n{n} tracked line ranges -> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
