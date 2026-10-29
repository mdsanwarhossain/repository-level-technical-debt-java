#!/usr/bin/env python3
"""
Stage 9 - matched within-repository non-AI baseline.

The pilot showed the Stack Overflow arm cannot carry a comparison: 9 Java
repositories with full working trees produced zero `so_code_url` hits and one
eligible `so_commit_url` event. Comparing AI-attributed code against *nothing*
is not a study, and comparing it against a different set of repositories
reintroduces exactly the confounding the design is meant to remove.

So the control becomes non-AI commits in the **same repositories**. For each AI
introduction event we draw matched control commits from its own repository:

  same repository   project age, domain, team, review culture and coding
                    standards are held constant by construction
  same era          control commit within +/- `--days` of the AI commit, so
                    both have comparable exposure and face the same codebase
  similar size      matched on added Java lines, because change size drives
                    both debt and change-proneness
  not attributable  the control must match none of the provenance patterns

What this buys, and it is the strongest thing in the design:

  * **Asymmetric exposure disappears.** Treatment and control are drawn from the
    same weeks of the same project's history, so neither has been observed
    longer than the other. This was the single most likely reason a reviewer
    rejects the AI-vs-SO comparison.
  * **Non-random assignment is contained.** Source is still not randomised, but
    every project-level confounder is differenced out rather than adjusted for.

A control is *not* a claim that the code was written without assistance - it is
a claim that it carries no provenance signal. Unmarked AI use therefore
contaminates the control arm and biases toward the null. Stated in the paper:
any effect found is a lower bound.

Usage:
    python -m pipeline.p9_baseline --per-event 2 --days 120
"""
from __future__ import annotations
import argparse, json, math, re, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
from .config import INTERIM, REPOS, MIN_COMMITS_AFTER_INTRO, MIN_PROJECT_AGE_DAYS
from .p1b_local_scan import COMMIT_PATTERNS
from .p3_clone_locate import (added_java_lines, commit_meta, history_after,
                              project_age_days, restore_tip, run)

# Any of these in a commit message disqualifies it as a control.
ATTRIBUTABLE = re.compile("|".join(f"(?:{p})" for p in COMMIT_PATTERNS.values()),
                          re.I)
# Merges and bulk mechanical commits are not comparable units of authored change.
MECHANICAL = re.compile(r"\b(merge|revert|bump|dependabot|renovate|"
                        r"reformat|re-?format|license header|generated|"
                        r"update changelog|prepare release)\b", re.I)


def java_commits(repo: Path) -> list[dict]:
    """Every commit touching Java, with date, author and added-line count."""
    out = run(["git", "log", "--no-merges", "--format=%H%x1f%aI%x1f%ae%x1f%s",
               "--", "*.java"], cwd=repo, check=False)
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4 and len(parts[0]) == 40:
            rows.append({"sha": parts[0], "date": parts[1],
                         "email": parts[2], "subject": parts[3]})
    return rows


def eligible_control(repo: Path, c: dict) -> dict | None:
    if ATTRIBUTABLE.search(c["subject"]) or MECHANICAL.search(c["subject"]):
        return None
    # The full message matters: trailers live in the body, not the subject.
    body = run(["git", "show", "-s", "--format=%B", c["sha"]], cwd=repo, check=False)
    if ATTRIBUTABLE.search(body):
        return None
    files = added_java_lines(repo, c["sha"])
    if not files:
        return None
    added = sum(b - a + 1 for r in files.values() for a, b in r)
    if added < 2:
        return None
    return {"files": files, "added_lines": added}


def match(treated: dict, pool: list[dict], repo: Path, k: int, days: int,
          used: set[str], resolved: dict[str, dict]) -> list[dict]:
    """Pick up to `k` matched controls for one treated event.

    Candidates are considered nearest-date-first and their diffs resolved
    lazily, because a repository can hold thousands of Java commits and diffing
    all of them to match a handful would dominate the runtime. Resolution is
    cached per repository across treated events.
    """
    t_date = datetime.fromisoformat(treated["intro_date"])
    t_size = max(treated["added_lines"], 1)

    window = [c for c in pool
              if c["sha"] not in used
              and c["sha"] != treated["intro_sha"]
              and abs((datetime.fromisoformat(c["date"]) - t_date).days) <= days]
    window.sort(key=lambda c: abs(
        (datetime.fromisoformat(c["date"]) - t_date).days))

    scored: list[tuple[float, dict]] = []
    for c in window:
        if len(scored) >= 4 * k:
            break                      # enough good candidates resolved
        if c["sha"] not in resolved:
            resolved[c["sha"]] = eligible_control(repo, c) or {}
        info = resolved[c["sha"]]
        if not info.get("added_lines"):
            continue
        dd = abs((datetime.fromisoformat(c["date"]) - t_date).days)
        # Log-scale size distance: a 10-vs-20-line gap matters more than
        # 300-vs-310. Era distance is normalised to the window.
        ds = abs(math.log(info["added_lines"] / t_size))
        scored.append((ds + dd / max(days, 1), c | info))

    scored.sort(key=lambda x: x[0])
    return [c for _, c in scored[:k]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-event", type=int, default=2,
                    help="control commits per AI introduction event")
    ap.add_argument("--days", type=int, default=120,
                    help="era window: how far from the treated commit a control may be")
    ap.add_argument("--source", action="append", default=None,
                    choices=["ai", "so"],
                    help="which treated arm(s) to match. Matching the Stack "
                         "Overflow arm to its OWN era's controls is what makes "
                         "the two arms comparable: AI and SO events are "
                         "separated by roughly eighty to one in exposure, so a "
                         "direct AI-vs-SO contrast is confounded by era, "
                         "whereas each arm's effect relative to its own "
                         "contemporaneous baseline is not.")
    a = ap.parse_args()

    f = INTERIM / "introduction_events.jsonl"
    if not f.exists():
        sys.exit("no introduction_events.jsonl - run p3_clone_locate first")
    events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    arms = a.source or ["ai"]
    treated = [e for e in events if e["source"] in arms and e["eligible"]]
    already = {e["matched_to"] for e in events
               if e.get("source") == "baseline" and e.get("matched_to")}
    treated = [e for e in treated if e["intro_sha"] not in already]
    print(f"{len(treated)} eligible events to match "
          f"(arms={arms}; {len(already)} treated events already have controls)")

    by_repo: dict[str, list[dict]] = {}
    for e in treated:
        by_repo.setdefault(e["repo"], []).append(e)

    controls, used_global = [], set()
    for full, evs in by_repo.items():
        repo = REPOS / full.replace("/", "__")
        if not (repo / ".git").exists():
            print(f"  ! {full}: not cloned")
            continue
        restore_tip(repo)
        print(f"\n[{full}] {len(evs)} treated events")

        pool = [c for c in java_commits(repo)
                if not ATTRIBUTABLE.search(c["subject"])
                and not MECHANICAL.search(c["subject"])]
        print(f"  {len(pool)} candidate control commits before diff checks")

        age = project_age_days(repo)
        used: set[str] = set()
        resolved: dict[str, dict] = {}
        for ev in evs:
            picks = match(ev, pool, repo, a.per_event, a.days, used, resolved)
            for c in picks:
                used.add(c["sha"])
                used_global.add(c["sha"])
                meta = commit_meta(repo, c["sha"])
                controls.append({
                    "channel": "baseline_nonai",
                    "source": "baseline",
                    "matched_to": ev["intro_sha"],
                    # Which arm this control belongs to. Each arm is compared
                    # against its own contemporaneous baseline; the two effects
                    # are then compared with each other, which is what removes
                    # the era confound between the arms.
                    "matched_source": ev["source"],
                    # Match quality, carried in the data so a weak match can be
                    # excluded in a sensitivity analysis instead of silently
                    # weakening the comparison.
                    "matched_added_lines": ev["added_lines"],
                    "size_ratio": round(c["added_lines"] / max(ev["added_lines"], 1), 3),
                    "era_gap_days": abs((datetime.fromisoformat(meta["date"])
                                         - datetime.fromisoformat(ev["intro_date"])).days),
                    "repo": full,
                    "stars": ev.get("stars"),
                    "intro_sha": c["sha"],
                    "intro_date": meta["date"],
                    "intro_subject": meta["subject"],
                    "evidence": "no provenance signal in commit message or body",
                    "files": c["files"],
                    "added_lines": c["added_lines"],
                    "commits_after_intro": history_after(repo, c["sha"]),
                    "project_age_days": age,
                    "eligible": True,
                })
            print(f"    {ev['intro_sha'][:8]} ({ev['added_lines']} lines) -> "
                  + (", ".join(f"{c['sha'][:8]}:{c['added_lines']}l" for c in picks)
                     or "NO MATCH"))

    for c in controls:
        c["eligible"] = (c["commits_after_intro"] >= MIN_COMMITS_AFTER_INTRO
                         and c["project_age_days"] >= MIN_PROJECT_AGE_DAYS)

    with f.open("a") as fh:
        for c in controls:
            fh.write(json.dumps(c) + "\n")
    # Report match quality explicitly. Large commits often have no
    # size-comparable neighbour inside the era window, and a reviewer will ask.
    if controls:
        ratios = sorted(c["size_ratio"] for c in controls)
        gaps = sorted(c["era_gap_days"] for c in controls)
        within2x = sum(1 for r in ratios if 0.5 <= r <= 2.0)
        mid = len(ratios) // 2
        print(f"\nmatch quality: median size ratio {ratios[mid]:.2f}, "
              f"{within2x}/{len(ratios)} ({100 * within2x / len(ratios):.0f}%) "
              f"within 2x on size; median era gap {gaps[len(gaps) // 2]}d")
        poor = [c for c in controls if not 0.5 <= c["size_ratio"] <= 2.0]
        if poor:
            print(f"  {len(poor)} pairs outside 2x - `size_ratio` is in the data, "
                  f"so run the analysis with and without them")

    matched = len({c["matched_to"] for c in controls})
    print(f"\n{len(controls)} control events "
          f"({sum(1 for c in controls if c['eligible'])} eligible) "
          f"matched to {matched}/{len(treated)} treated events")
    print(f"-> appended to {f}")
    if matched < len(treated):
        print(f"{len(treated) - matched} treated events found no control inside "
              f"the {a.days}-day era window - widen --days or accept the loss, "
              f"but report how many were dropped.")


if __name__ == "__main__":
    sys.exit(main())
