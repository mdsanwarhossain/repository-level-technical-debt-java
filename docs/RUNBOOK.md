# Runbook

Everything runs from the project root with the bundled virtualenv:

```bash
cd "New Paper repository level thesis"
./env/bin/python -m pipeline.<stage>
```

## One-time setup

**GitHub token** — the search endpoints require authentication; code search is
unusable without it. A fine-grained token with *public repositories, read-only*
is enough.

```bash
echo '<token>' > .github_token     # git-ignored
```

**SonarQube** — already running: community edition 26.10 in Docker, admin
password reset, analysis token written to `.sonar_token` (git-ignored, 0600).
Stage 4 picks the token up automatically. To bring it back after a reboot:

```bash
docker start sonarqube || docker run -d --name sonarqube -p 9000:9000 \
  -v sonar_data:/opt/sonarqube/data -v sonar_exts:/opt/sonarqube/extensions \
  sonarqube:community
# http://localhost:9000  — first login admin/admin
```

**sonar-scanner CLI** — already installed at
`tools/sonar-scanner-7.0.2.4839-linux-x64/`; stage 4 finds it there, no `PATH`
changes needed.

**DesigniteJava** — at `tools/DesigniteJava.jar`, driven through
`pipeline/designite.py`. That module is written against the jar's *actual*
output, which does not match its documentation: the CSVs are camelCase
(`designCodeSmells.csv`), there is no `File path` column (attribution goes
through `Package Name` + `Type Name`), and there is no architecture-smells file
(the repository-scoped smells are inside `designCodeSmells.csv`, identified by
name in `ARCHITECTURE_LEVEL`). It raises on an empty result rather than
reporting "no smells".

## The pilot (do this first — it is the go/no-go gate)

Stage 1 needs the token. Stage 1b does not — it scans clones directly and has
strictly higher recall per repository, so prefer it once a corpus exists.

```bash
# 1. sample the Java corpus (repository search supports language:, commit search does not)
./env/bin/python -m pipeline.p0_sample_corpus --target 360

# 2. blobless discovery: commit history without file contents, keepers only
./env/bin/python -m pipeline.p1b_local_scan --corpus --blobless

# 3. optional: global channel yields, for the paper's descriptive statistics
./env/bin/python -m pipeline.p1_provenance_probe --cap 100
./env/bin/python -m pipeline.p2_devgpt_extract --schema     # inspect
./env/bin/python -m pipeline.p2_devgpt_extract
./env/bin/python -m pipeline.p2b_enrich                     # language/stars/age
./env/bin/python -m pipeline.p3_clone_locate \
    --channel so_code_url --channel ai_trailer_claude \
    --channel ai_trailer_copilot --channel ai_devgpt --limit 20
./env/bin/python -m pipeline.p7_validate --make-sheet --per-channel 100
# two raters fill data/final/validation/rater_{a,b}.csv
./env/bin/python -m pipeline.p7_validate --score
```

Read `data/final/validation_report.txt`. Per-channel precision below ~80% means
stop and redesign that channel rather than scaling it.

The sheets exclude the `baseline_nonai` arm: it is defined by the *absence* of a
signal, so there is no attribution to rate. The `evidence` column holds the
actual attributing line (the trailer or the cited URL), not the commit subject.

## Full run

```bash
./env/bin/python -m pipeline.p1b_local_scan --corpus --blobless   # resumable
./env/bin/python -m pipeline.p1c_promote                          # keepers -> full
./env/bin/python -m pipeline.p2b_enrich                           # metadata
./env/bin/python -m pipeline.p3_clone_locate --channel <each> --limit 400
./env/bin/python -m pipeline.p9_baseline --per-event 2 --days 120
./env/bin/python -m pipeline.p10_so_clones
./env/bin/python -m pipeline.p4_measure --designite --sonar       # the long one
./env/bin/python -m pipeline.p5_track
./env/bin/python -m pipeline.p6_analyze
```

Discovery is resumable: `data/interim/discovery_log.jsonl` records a verdict per
repository and already-scanned ones are skipped, so an interrupted pass can just
be restarted. Candidates are persisted per repository, not at the end.

## Baseline arm (the control)

```bash
./env/bin/python -m pipeline.p9_baseline --per-event 2 --days 120
```

Appends matched non-AI control events from the same repositories to
`introduction_events.jsonl` with `source="baseline"`. Check the reported match
quality (median size ratio, % within 2x, median era gap); `size_ratio` is on
every row so weak pairs can be excluded in a sensitivity analysis.

## Stack Overflow arm

```bash
./env/bin/python -m pipeline.p10_so_clones          # defaults are the tuned ones
```

Clone detection against the 500-answer snippet corpus. The defaults
(`--min-grams 30 --min-shared 25 --min-containment 0.8`, decompiled and
generated trees excluded) were set *after* an untuned run returned 107 matches
of which 103 were decompiled bytecode. Do not loosen them without re-reading
§"The SO arm had to be rebuilt" in the protocol.

Needs promoted (full) clones - a blobless clone has no working tree to scan.

## Injection sub-study — dropped

`p8_injection` is retained for reproducibility but **RQ5 was tested and
dropped**: 6 of 6 snippet-variants gave identical deltas across both hosts, and
the only architecture-level change was `Unutilized Abstraction +1`, an artifact
of adding an unreferenced class. See protocol §4. Do not spend corpus time on
it.

```bash
./env/bin/python -m pipeline.p8_injection --pick-hosts     # ~7s per analysis
./env/bin/python -m pipeline.p8_injection --run --hosts 2 --snippets 3
```

## Disk

Full-history clones are mandatory for survival analysis, so the corpus is the
space constraint: 360 repositories project to ~35 GB. Run discovery blobless and
promote only the keepers:

```bash
git -C repos/<name> fetch --refetch origin
git -C repos/<name> checkout <default-branch>
```

## Layout

```
pipeline/     stages p0..p10, config.py, github_api.py, store.py, designite.py
tools/        DesigniteJava.jar
data/raw/     DevGPT SQLite (git-ignored)
data/interim/ java_corpus.jsonl, discovery_log.jsonl, candidates_*.jsonl,
              repo_metadata.json, introduction_events.jsonl (treated + baseline),
              measurements.jsonl, survival_rows.jsonl, injection_results.jsonl
data/final/   analysis report, RQ tables, validation sheets
repos/        clones, full history (git-ignored; expect tens of GB at full scale)
docs/         PROTOCOL_v2.md, RUNBOOK.md
```


## Building the manuscript

The manuscript is `Main Document/repo_level_td.tex`; the built PDF is copied to
`manuscript_repository_level.pdf` in the project root.

The Wiley template does not build on TeX Live 2026 as shipped, so the build uses
a `build/` directory holding the template files **except** `listings.sty`:

```bash
rm -rf build && mkdir -p build
cd "Main Document"
cp *.cls *.bst *.bib *.png *.jpg ../build/
for f in *.sty; do [ "$f" = "listings.sty" ] || cp "$f" ../build/; done
cp LETTERSP.STY ../build/lettersp.sty
cp -r Fonts ../build/
cp repo_level_td.tex ../build/
cd ../build
docker run --rm -v "$PWD":/w -w /w texlive/texlive:latest bash -c \
  "xelatex -interaction=nonstopmode repo_level_td.tex; \
   bibtex repo_level_td; \
   xelatex -interaction=nonstopmode repo_level_td.tex; \
   xelatex -interaction=nonstopmode repo_level_td.tex"
cp repo_level_td.pdf ../manuscript_repository_level.pdf
```

Three template problems and their fixes:

1. `WileyNJDv5.cls` calls `\reserveinserts` (its line 356) from the obsolete
   `etex.sty`. The manuscript prepends `\providecommand\reserveinserts[1]{}`
   **before** `\documentclass`.
2. The template bundles `listings.sty` v1.8d, which modern `lstmisc.sty`
   rejects. Omitting it from the build dir lets the distribution's copy load.
3. The class needs XeTeX (`pdflatex` fails in `fontspec`) and the bundled
   `Fonts/` directory, and wants `lettersp.sty` in lowercase.

Docker cannot mount the scratchpad here, so `build/` must live inside the
project. The pre-existing `wileyNJDv5_AMA.tex` fails identically without these
fixes, so they are environmental rather than specific to this manuscript.

## Regenerating the analysis

```bash
./env/bin/python -m pipeline.p6_analyze      # re-reads interim data, no mining
```

`data/final/analysis_report.txt` is the text the Results section is written
from; `event_level_table.csv` holds the per-commit sensitivity data.
