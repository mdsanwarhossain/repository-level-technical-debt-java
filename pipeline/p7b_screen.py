#!/usr/bin/env python3
"""
Stage 7b - automated screen of attribution quality.

This does NOT replace the blinded human protocol of p7_validate, and nothing it
produces should be reported as semantic precision. Whether a trailer-bearing
commit genuinely introduces tool-written code is a judgement about authorship
that no script can make.

What it does measure is a set of reproducible, falsifiable properties that bear
on precision, each of which would be invisible otherwise:

  well-formed trailer   Git trailers belong in the final paragraph of a commit
                        message and match `^Key: Value$`. A tool that emits a
                        trailer emits a well-formed one. The same string
                        appearing mid-message, indented, or inside quoted text
                        is far weaker evidence - typically a pull-request body
                        pasted into the message, or a changelog entry.
  revert                A commit whose message marks it a revert is not an
                        introduction, whatever it carries.
  substantive change    Added Java lines that are not purely imports,
                        annotations, braces or blank - i.e. the commit actually
                        introduces logic.
  co-authored           The trailer names a co-author distinct from the commit
                        author, which is the shape a genuine agent trailer has.

Reported per channel, these bound the attribution from above: an event that
fails `well-formed` or is a revert is very unlikely to be a true positive, so
the pass rate is an optimistic ceiling on precision, not an estimate of it.

Usage:
    python -m pipeline.p7b_screen
"""
from __future__ import annotations
import json, re, subprocess, sys
from collections import Counter, defaultdict
from .config import INTERIM, FINAL, REPOS

TRAILER_KEY = re.compile(r"^(co-authored-by|signed-off-by|assisted-by|generated-by)\s*:\s*\S",
                         re.I)
AGENT = re.compile(r"\b(claude|copilot|cursor|codex|devin|aider)\b", re.I)
SO_URL = re.compile(r"https?://(?:[a-z]+\.)?stackoverflow\.com/(?:a|questions)/\d+", re.I)
REVERT = re.compile(r"^\s*(revert|rollback|back\s?out)\b", re.I)
TRIVIAL_LINE = re.compile(r"^\s*(import\s|package\s|//|/\*|\*|@\w+\s*$|[{}();]*\s*$)")


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True).stdout


def final_block(message: str) -> list[str]:
    """The last non-empty paragraph, where git trailers live."""
    paras = [p for p in re.split(r"\n\s*\n", message.strip()) if p.strip()]
    return paras[-1].splitlines() if paras else []


def screen_event(ev: dict) -> dict:
    repo = REPOS / ev["repo"].replace("/", "__")
    if not (repo / ".git").exists():
        return {"status": "repo-missing"}
    msg = run(["git", "show", "-s", "--format=%B", ev["intro_sha"]], cwd=repo)
    subject = msg.splitlines()[0] if msg.strip() else ""
    is_so = ev["channel"].startswith("so_")

    if is_so:
        # For the citation channel "well formed" means the URL is a real answer
        # reference in the message, not an artefact of a pasted diff.
        well_formed = bool(SO_URL.search(msg))
    else:
        well_formed = any(
            TRAILER_KEY.match(l.strip()) and AGENT.search(l)
            for l in final_block(msg))

    added = run(["git", "show", "--unified=0", "--format=", ev["intro_sha"]],
                cwd=repo)
    java_added = [l[1:] for l in added.splitlines()
                  if l.startswith("+") and not l.startswith("+++")]
    substantive = sum(1 for l in java_added if not TRIVIAL_LINE.match(l))

    author = run(["git", "show", "-s", "--format=%ae", ev["intro_sha"]],
                 cwd=repo).strip().lower()
    coauth = [l for l in msg.splitlines()
              if l.lower().strip().startswith("co-authored-by:")]
    distinct_coauthor = any(author not in l.lower() for l in coauth) if coauth else False

    return {
        "status": "screened",
        "well_formed": well_formed,
        "revert": bool(REVERT.match(subject)),
        "substantive_added_lines": substantive,
        "substantive": substantive >= 3,
        "distinct_coauthor": distinct_coauthor,
    }


def main():
    f = INTERIM / "introduction_events.jsonl"
    if not f.exists():
        sys.exit("no introduction_events.jsonl")
    events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    treated = [e for e in events if e["source"] != "baseline"]
    print(f"screening {len(treated)} attributed introduction events\n")

    per = defaultdict(Counter)
    rows = []
    for e in treated:
        r = screen_event(e)
        if r["status"] != "screened":
            per[e["channel"]]["skipped"] += 1
            continue
        c = per[e["channel"]]
        c["n"] += 1
        c["well_formed"] += r["well_formed"]
        c["revert"] += r["revert"]
        c["substantive"] += r["substantive"]
        c["distinct_coauthor"] += r["distinct_coauthor"]
        passes = r["well_formed"] and not r["revert"] and r["substantive"]
        c["pass"] += passes
        rows.append({**{k: e[k] for k in ("channel", "repo", "intro_sha")},
                     **r, "pass": passes})

    hdr = f"{'channel':22s} {'n':>5s} {'well-formed':>12s} {'revert':>7s} {'substantive':>12s} {'ceiling':>9s}"
    print(hdr); print("-" * len(hdr))
    tot = Counter()
    for ch in sorted(per):
        c = per[ch]
        n = c["n"] or 1
        for k in ("n", "well_formed", "revert", "substantive", "pass"):
            tot[k] += c[k]
        print(f"{ch:22s} {c['n']:>5d} {100*c['well_formed']/n:>11.1f}% "
              f"{100*c['revert']/n:>6.1f}% {100*c['substantive']/n:>11.1f}% "
              f"{100*c['pass']/n:>8.1f}%")
    n = tot["n"] or 1
    print("-" * len(hdr))
    print(f"{'ALL':22s} {tot['n']:>5d} {100*tot['well_formed']/n:>11.1f}% "
          f"{100*tot['revert']/n:>6.1f}% {100*tot['substantive']/n:>11.1f}% "
          f"{100*tot['pass']/n:>8.1f}%")

    out = FINAL / "attribution_screen.jsonl"
    with out.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"\n-> {out}")
    print("\nThe 'ceiling' column is an upper bound on precision, not an estimate: "
          "it counts events that are not disqualified. Semantic precision still "
          "requires the blinded human protocol in p7_validate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
