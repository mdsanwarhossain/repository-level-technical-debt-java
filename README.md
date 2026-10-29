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
| Repositories promoted / contributing tracked events | 56 / 43 |
| Introduction events located (eligible) | 612 (600) |
| Matched within-repository controls, both arms | 1,198 (1,180) |
| Tracked line ranges | 62,706 over 1,771 events |

## Headline results

1. **Provenance is the bottleneck, not measurement.** DevGPT yields exactly
   **one** usable Java repository (82% of its commit links come from a single
   non-Java project). GitHub commit search admits no `language:` qualifier, and
   **no Java repository appeared in 500 sampled trailer commits**. Stack
   Overflow answer URLs are **absent** from Java source comments — zero hits
   across all promoted repositories.
2. **Two analysis choices decide the answer.** Asking whether AI-attributed
   code is disturbed less often than matched code in the same project gives
   four answers depending on how line ranges and exposure are handled:

   | Analysis | Ratio (95% CI) | *p* |
   |---|---|---|
   | Unpaired, exposure-unadjusted | 0.67 (0.54–0.84) | 0.0003 |
   | Cox, stratified + clustered SEs | 0.45 (0.21–0.95) | 0.035 |
   | Negative binomial, exposure offset | 0.82 (0.64–1.05) | 0.115 |
   | **Paired, exposure-balanced (McNemar)** | **0.91 (0.59–1.40)** | **0.74** |

   One commit contributes a mean of 34 line ranges, which are not independent;
   and matched controls carry a mean of 6.3 days more exposure than the events
   they match, giving them more opportunity to be disturbed. Controlling for
   both removes the effect. **We report no reliable difference.**

5. **AI and crowdsourced code behave identically, era confound removed.** Each
   arm matched to its *own* period's controls: AI vs its baseline OR 0.83
   (*p* = 0.134), Stack Overflow vs its baseline OR 0.91 (*p* = 0.878), and the
   two arms do not differ (*p* = 0.781).

6. **Remediation cost at introduction is rare and heavy-tailed.** Median SQALE
   effort is **0 minutes in every arm**; 2.5–12.5% of events carry any issue on
   the lines they added. One Stack Overflow commit holds 410 of its arm's 417
   issues and 12,300 of 12,363 minutes — 98% of the total from one of forty
   events.
3. **Injection cannot make a snippet study repository-level.** Six of six
   snippet variants produced identical measurements across two architecturally
   distinct hosts, and the only architecture-level change was an artifact of
   adding an unreferenced class. A new compilation unit has no architectural
   relationship to its host and no subsequent history.
4. **Attribution is mechanically sound but not semantically validated.** Every
   located event was confirmed to carry the claimed evidence in its commit
   message, and an automated screen over all 612 events puts an upper bound of
   **87.3%** on precision (97.2% well-formed trailers, 0.3% reverts). Whether a trailer-bearing commit genuinely introduces
   tool-written code requires human rating, which we specify and release but did
   not execute — the study's principal limitation.

## Layout

```
pipeline/        p0–p10 analysis stages, plus config, store, designite,
                 github_api and the figure generator
data/interim/    the mined corpus — see "Dataset" below
data/final/      analysis outputs: the report the paper is written from,
                 per-RQ tables, KM curves, and the blinded validation sheets
figures/         the manuscript figures, as published (PDF and EPS)
docs/            full protocol and an operational runbook
RESULTS_MAP.md   every reported number, and the file and command behind it
ENVIRONMENT.md   tool versions and analyser caveats
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

## Verifying the paper's numbers

**`RESULTS_MAP.md` maps every claim in the paper to the file that contains it
and the command that regenerates it.** Start there.

The manuscript itself is not included in this repository while the paper is
under review.

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
