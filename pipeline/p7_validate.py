#!/usr/bin/env python3
"""
Stage 7 - manual validation of provenance attribution.

Without this the study is unfalsifiable: every number downstream assumes the
attribution is correct, and no reviewer will take that on trust. The protocol is
fixed in advance:

  * >= 100 sampled attributions per channel, drawn at random with a fixed seed
  * two independent raters, blind to each other
  * Cohen's kappa for agreement, per-channel precision with a Wilson interval
  * disagreements resolved by discussion, and the resolution reported

`--make-sheet` writes one blinded CSV per rater. Raters fill the `verdict`
column with yes / no / unclear:

    yes      the commit genuinely introduces code from the stated source
    no       false attribution (a revert, a mention in prose, a licence file,
             a comment about the source rather than code from it)
    unclear  cannot tell from the diff alone

`--score` reads the filled sheets back and reports kappa and precision.

Usage:
    python -m pipeline.p7_validate --make-sheet --per-channel 100
    python -m pipeline.p7_validate --score
"""
from __future__ import annotations
import argparse, csv, json, random, subprocess, sys
from pathlib import Path
from .config import INTERIM, FINAL, REPOS

SEED = 20261028
RATERS = ("rater_a", "rater_b")
SHEETS = FINAL / "validation"


def diff_excerpt(repo: Path, sha: str, limit: int = 60) -> str:
    p = subprocess.run(["git", "show", "--stat", "--unified=3", "--format=%s%n%n", sha],
                       cwd=repo, capture_output=True, text=True)
    lines = p.stdout.splitlines()[:limit]
    return "\n".join(lines)


def make_sheets(per_channel: int):
    f = INTERIM / "introduction_events.jsonl"
    if not f.exists():
        sys.exit("no introduction_events.jsonl - run p3_clone_locate first")
    events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]

    # The matched baseline arm is defined by the *absence* of a provenance
    # signal, so there is no attribution to validate and a rater cannot tell
    # from a diff whether unmarked code was assisted. Including it would spend
    # half the rating effort on an unanswerable question. Contamination of the
    # control arm is handled as a stated threat in p9_baseline, not here.
    events = [e for e in events if e["channel"] != "baseline_nonai"]

    by_channel: dict[str, list] = {}
    for e in events:
        by_channel.setdefault(e["channel"], []).append(e)

    rnd = random.Random(SEED)
    sample = []
    for ch, evs in sorted(by_channel.items()):
        take = evs if len(evs) <= per_channel else rnd.sample(evs, per_channel)
        if len(evs) < per_channel:
            print(f"  ! {ch}: only {len(evs)} attributions available, "
                  f"protocol asks for {per_channel}")
        sample.extend(take)
    rnd.shuffle(sample)   # blind the raters to channel ordering

    SHEETS.mkdir(parents=True, exist_ok=True)
    for rater in RATERS:
        path = SHEETS / f"{rater}.csv"
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "repo", "intro_sha", "evidence", "added_lines",
                        "diff_excerpt", "verdict", "note"])
            for i, e in enumerate(sample, 1):
                repo = REPOS / e["repo"].replace("/", "__")
                excerpt = diff_excerpt(repo, e["intro_sha"]) if (repo / ".git").exists() else ""
                w.writerow([i, e["repo"], e["intro_sha"], e["evidence"],
                            e["added_lines"], excerpt, "", ""])
        print(f"  -> {path}")

    key = SHEETS / "_key.csv"
    with key.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "channel", "source", "repo", "intro_sha"])
        for i, e in enumerate(sample, 1):
            w.writerow([i, e["channel"], e["source"], e["repo"], e["intro_sha"]])
    print(f"  -> {key}  (channel labels, kept out of the rater sheets)")
    print(f"\n{len(sample)} attributions to rate, {len(RATERS)} raters.")


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return p, c - h, c + h


def score_plain():
    import pandas as pd
    sheets = {}
    for rater in RATERS:
        p = SHEETS / f"{rater}.csv"
        if not p.exists():
            sys.exit(f"missing {p} - run --make-sheet first, then have raters fill it")
        d = pd.read_csv(p).set_index("id")
        if d["verdict"].isna().all():
            sys.exit(f"{p} has no verdicts filled in yet")
        sheets[rater] = d["verdict"].astype(str).str.strip().str.lower()
    key = pd.read_csv(SHEETS / "_key.csv").set_index("id")

    a, b = sheets[RATERS[0]], sheets[RATERS[1]]
    both = a.index.intersection(b.index)
    a, b = a.loc[both], b.loc[both]

    # Cohen's kappa, computed directly so sklearn is not a dependency.
    cats = sorted(set(a) | set(b))
    n = len(both)
    obs = (a.to_numpy() == b.to_numpy()).mean()
    exp = sum((a == c).mean() * (b == c).mean() for c in cats)
    kappa = (obs - exp) / (1 - exp) if exp < 1 else float("nan")

    out = ["Provenance validation", "=" * 40, "",
           f"rated: {n} attributions by {len(RATERS)} raters",
           f"raw agreement: {100 * obs:.1f}%",
           f"Cohen's kappa: {kappa:.3f}", ""]

    agreed = a[a == b]
    out.append("per-channel precision (agreed ratings only):")
    merged = key.loc[agreed.index].assign(verdict=agreed)
    for ch, g in merged.groupby("channel"):
        yes = int((g["verdict"] == "yes").sum())
        tot = int((g["verdict"] != "unclear").sum())
        p, lo, hi = wilson(yes, tot)
        out.append(f"  {ch:22s} {yes}/{tot} = {100 * p:.1f}% "
                   f"(95% CI {100 * lo:.1f}-{100 * hi:.1f})")
    disagreed = both.difference(agreed.index)
    out.append(f"\n{len(disagreed)} disagreements to resolve by discussion: "
               f"{list(disagreed[:20])}")

    report = "\n".join(out)
    (FINAL / "validation_report.txt").write_text(report)
    print(report)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-sheet", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--per-channel", type=int, default=100)
    a = ap.parse_args()
    if a.make_sheet:
        make_sheets(a.per_channel)
    elif a.score:
        score_plain()
    else:
        ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
