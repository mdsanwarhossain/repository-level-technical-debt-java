# Provenance and Survival of Technical Debt in AI-Assisted and Crowdsourced Java Code

Replication package for the paper *"From Snippets to Repositories: Provenance
and Survival of Technical Debt in AI-Assisted and Crowdsourced Java Code"*.

This repository contains the full analysis pipeline, the mined dataset, the
statistical outputs the paper's tables and figures are built from, and the
manuscript source. Everything reported in the paper can be recomputed from what
is here, except the repository clones themselves (tens of gigabytes), which the
pipeline re-creates from the corpus manifest.

---

## What the study does

The unit of analysis is the **introduction event**: a commit in which code
traceable to an external source — an AI coding agent or a Stack Overflow
answer — enters a maintained Java repository. Each event is measured at its
introduction commit and then followed forward through the project's history,
against a control group of non-attributed commits drawn from the **same
repository in the same period**.

## Corpus at a glance

| | |
|---|---|
| Java repositories sampled (6 star bands) | 360 |
| Repositories carrying ≥1 provenance signal | 134 (37%) |
| `ai_trailer_claude` / `copilot` / `cursor` / `codex` / `devin` commits | 5,735 / 772 / 164 / 19 / 9 |
| `so_commit_url` commits | 141 |
| Repos with AI trailer / SO citation / **both** | 107 / 49 / **31** |
| Repositories promoted to full clones and measured | 18 |
| Introduction events located (eligible) | 320 (313) |
| Matched within-repository controls (eligible) | 576 (567) |
| Tracked line ranges | 28,374 over 877 events |

## Headline results

1. **Provenance is the bottleneck, not measurement.** DevGPT yields exactly
   **one** usable Java repository (82% of its commit links come from a single
   non-Java project). GitHub commit search admits no `language:` qualifier, and
   **no Java repository appeared in 500 sampled trailer commits**. Stack
   Overflow answer URLs are **absent** from Java source comments — zero hits
   across all promoted repositories.
2. **Inference choices reverse the conclusion.** One introduction commit
   contributes a mean of 32.8 line ranges (max 6,604); those rows are not
   independent. Clustering standard errors on the commit leaves every point
   estimate unchanged and widens every interval by about an order of magnitude:

   | Model | Naive *p* | Clustered *p* |
   |---|---|---|
   | Cox, AI vs. matched baseline (HR 0.27) | 7×10⁻¹⁸³ | **0.013** |
   | Cox, AI vs. Stack Overflow (HR 0.42) | 3×10⁻¹¹ | **0.078** |
   | Negative binomial, file touches (IRR 0.86) | 2×10⁻²⁰ | **0.319** |

   The event-level analysis (one row per commit) finds no difference at all
   (Fisher *p* = 0.599, Cliff's δ = −0.04). **We report no reliable difference.**
3. **Injection cannot make a snippet study repository-level.** Six of six
   snippet variants produced identical measurements across two architecturally
   distinct hosts, and the only architecture-level change was an artifact of
   adding an unreferenced class. A new compilation unit has no architectural
   relationship to its host and no subsequent history.
4. **Attribution is mechanically sound but not semantically validated.** All
   **320/320** located events were confirmed to carry the claimed evidence in
   their commit message. Whether a trailer-bearing commit genuinely introduces
   tool-written code requires human rating, which we specify and release but did
   not execute — the study's principal limitation.

## Layout

```
pipeline/        p0–p10 analysis stages, plus config, store, designite,
                 github_api and the figure generator
data/interim/    the mined corpus — see "Dataset" below
data/final/      analysis outputs: the report the paper is written from,
                 per-RQ tables, KM curves, and the blinded validation sheets
figures/         the manuscript figures, as published
manuscript/      LaTeX source, bibliography and compiled PDF
docs/            full protocol and an operational runbook
```

## Dataset

Every file is JSON Lines (one JSON object per line) unless noted.

| File | Rows | What it is |
|---|---|---|
| `java_corpus.jsonl` | 360 | The sampling frame: repositories with star band and metadata |
| `discovery_log.jsonl` | 360 | Per-repository keep/discard verdict and channel hits — the audit trail for corpus attrition |
| `candidates_<channel>.jsonl` | varies | Raw provenance candidates per channel, with commit message and repository metadata |
| `introduction_events.jsonl` | 896 | Located events: commit, date, attributing evidence, affected files with added line ranges, eligibility. Includes the 576 matched controls (`source: "baseline"`) |
| `survival_rows.jsonl` | 28,374 | One row per tracked line range: exposure, censoring, time-to-first-change, file touches, fix touches |
| `measurements.jsonl` | 28 | SonarQube SQALE and DesigniteJava output per measured event |
| `repo_metadata.json` | 214 | Cached GitHub repository records (language, stars, fork/archived, dates) |
| `injection_results.jsonl` | 12 | The injection sub-study measurements (reported as a negative result) |

`data/final/` additionally holds `analysis_report.txt` (the text the Results
section is written from), `event_level_table.csv` (the per-commit sensitivity
analysis), `rq3_km_curves.csv`, the Cox and negative-binomial summaries, and
`validation/` — the blinded rater sheets and their key.

**Note on `validation/_key.csv`:** it maps sheet rows to provenance channels. It
is published for transparency but must be withheld from raters, or the blinding
is broken.

## Reproducing

```bash
python3 -m venv env && ./env/bin/pip install -r requirements.txt
```

The analysis stages re-run from the committed data with no network access:

```bash
./env/bin/python -m pipeline.p6_analyze        # regenerates data/final/
./env/bin/python -m pipeline.make_figures      # regenerates figures/
```

Re-mining from scratch needs a GitHub token (`.github_token`, or `GITHUB_TOKEN`),
a SonarQube instance, and substantial disk. `docs/RUNBOOK.md` gives the full
sequence, the disk-management strategy (blobless discovery, then promotion of
keepers only), and the three fixes the Wiley LaTeX template needs to build on
TeX Live 2026.

## Third-party data and tools

- **DevGPT** (Zenodo [10.5281/zenodo.10603053](https://doi.org/10.5281/zenodo.10603053)), CC-BY-4.0. Not redistributed here; `pipeline/p2_devgpt_extract.py` consumes it directly.
- **DesigniteJava**, **SonarQube** (Community Edition, SQALE). Not redistributed.
- Repository contents remain under their original licences; only derived
  measurements and commit identifiers are published here.

## Licence

Code and derived data in this repository: MIT (see `LICENSE`).
