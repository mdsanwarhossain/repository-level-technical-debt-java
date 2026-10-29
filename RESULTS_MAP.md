# Where every reported number comes from

This file maps each claim in the paper to the file that contains it and the
command that regenerates it. Everything listed here can be reproduced from the
data committed in this repository, with **no network access and no repository
clones** — the mining stages have already been run and their output is here.

```bash
python3 -m venv env && ./env/bin/pip install -r requirements.txt
./env/bin/python -m pipeline.p6_analyze      # regenerates data/final/
./env/bin/python -m pipeline.p7b_screen      # regenerates the attribution screen
./env/bin/python -m pipeline.make_figures    # regenerates figures/
```

`data/final/analysis_report.txt` is the single file the Results section is
written from. Every statistic below appears in it verbatim.

---

## Corpus

| Claim | Value | Source |
|---|---|---|
| Java repositories sampled | 360 | `data/interim/java_corpus.jsonl` (one row per repo) |
| Repositories carrying a provenance signal | 134 | `data/interim/discovery_log.jsonl`, `verdict == "keep"` |
| Repositories with AI / SO / both | 107 / 49 / 31 | `discovery_log.jsonl`, `channels` field |
| Introduction events located (eligible) | 612 (600) | `data/interim/introduction_events.jsonl`, `source != "baseline"` |
| Matched controls (eligible) | 1,198 (1,180) | same file, `source == "baseline"` |
| Tracked line ranges / events / repos | 62,706 / 1,771 / 43 | `data/interim/survival_rows.jsonl` |

## Provenance (RQ1)

| Claim | Source |
|---|---|
| Commit-search totals per trailer | `data/interim/probe_summary.json` |
| DevGPT: 697 pairs, 78 repos, 1 Java-eligible | `data/interim/candidates_ai_devgpt.jsonl` + `repo_metadata.json` |
| `so_code_url` returned zero hits | `data/interim/so_clone_summary.json`; no `candidates_so_code_url.jsonl` exists |
| Channel yields per repository | `data/interim/discovery_log.jsonl` |

## Attribution screen

| Claim | Value | Source |
|---|---|---|
| Well-formed trailers / reverts / ceiling | 97.2% / 0.3% / 87.3% | `data/final/attribution_screen.jsonl`; rerun `p7b_screen` |
| Second independent marker (Claude / Cursor / Copilot) | 94.4% / 100% / 28.8% | same |

## Survival and process (RQ2, RQ3)

All in `data/final/analysis_report.txt`; row-level data in
`data/final/survival_table.csv` and `data/final/event_level_table.csv`.

| Claim | Value |
|---|---|
| Cox, stratified by repo, clustered SEs | HR 0.45 (0.21–0.95), p = 0.035 |
| Negative binomial, exposure offset | IRR 0.82 (0.64–1.05), p = 0.115 |
| Unpaired, exposure-unadjusted | OR 0.67 (0.54–0.84), p = 0.0003 |
| Paired, exposure-balanced (McNemar) | OR 0.91, p = 0.74 |
| Exposure imbalance between matched pairs | mean +6.3 days |

Model objects: `rq3_cox_stratified.csv`, `rq4_nb_file_touches.csv`,
`rq3_km_curves.csv` (the Kaplan–Meier figure's underlying curves).

## AI versus crowdsourced, era-free

| Claim | Value | Source |
|---|---|---|
| AI vs its own baseline | OR 0.83 (0.65–1.05), p = 0.134 | `analysis_report.txt`, "ARM vs ITS OWN CONTEMPORANEOUS BASELINE" |
| Stack Overflow vs its own baseline | OR 0.91 (0.50–1.67), p = 0.878 | same |
| Difference between arms | 0.91, z = −0.28, p = 0.781 | same |

The arm each control belongs to is the `matched_source` field in
`introduction_events.jsonl`.

## Remediation cost (RQ2 continued)

`data/interim/measurements.jsonl` — one record per measured event, holding the
SonarQube issue list with severity and effort in minutes, and the DesigniteJava
counts.

| Claim | Value |
|---|---|
| Events measured (AI / baseline / SO) | 119 (39 / 40 / 40) |
| Events with any issue on introduced lines | 2 / 1 / 5 |
| Median effort, every arm | 0 min |
| Outlier commit | `dustin/java-memcached-client` holds 410 of the SO arm's 417 issues |

## Injection sub-study (reported as a negative result)

`data/interim/injection_results.jsonl` — per-host, per-variant smell deltas.
The identical-across-hosts property is visible by grouping on
`(question_id, variant)` and comparing the `delta` objects.

## Figures

| Figure | Script | Data |
|---|---|---|
| Corpus attrition | `pipeline/make_figures.py::fig_corpus` | counts above |
| One comparison, four analyses | `::fig_clustering` | `analysis_report.txt` |
| Kaplan–Meier survival | `::fig_km` | `data/final/rq3_km_curves.csv` |

## Validation instrument

`data/final/validation/` holds the blinded rater sheets and their key. The
human dual-rating protocol described in the paper was **not executed**; the
sheets are released so it can be. `_key.csv` maps rows to channels and must be
withheld from raters or the blinding is broken.
