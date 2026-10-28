# Repository-Level Technical Debt and Remediation Cost in AI-Assisted vs. Crowdsourced Java Code

**Protocol v2 — hybrid design.** Supersedes `../JSEP_study_protocol.md` for this
study. Dataset is built from scratch; the 500-pair snippet corpus is reused only
as stimulus material for the injection sub-study (§4), never as the primary unit
of analysis.

Decided 2026-10-28: mining is the primary design, injection is a controlled
sub-study.

---

## 1. Why the snippet corpus cannot simply be extended

The editor's objection is that debt measured on an isolated snippet is not debt.
Three of the indicators in the snippet study are not even definable without a
project around them — architecture smells (cyclic dependency, god component,
scattered functionality) are repository-scoped by construction, and DesigniteJava
cannot report them on a fragment.

Two things follow:

1. The unit of analysis must become the **introduction event** — a commit where
   code traceable to an external source enters a maintained repository.
2. The outcome must become **severity and remediation cost over time**, not
   indicator presence. DesigniteJava reports neither; SonarQube's SQALE model
   reports both directly, in minutes per issue.

## 2. Research questions

| RQ | Question | Design |
|---|---|---|
| RQ1 | What fraction of the debt in a reuse candidate survives adoption, rather than being cleaned up on the way in? | mining + injection |
| RQ2 | What is the severity distribution and SQALE remediation effort of debt introduced from each source? | mining |
| RQ3 | How long does source-attributed debt survive, and how much is ever repaid? | mining |
| RQ4 | Is source-derived code more change-prone or fix-prone than its surroundings? | mining |
| ~~RQ5~~ | ~~Controlling for the host, does a snippet introduce more architectural debt when integrated?~~ **Dropped** — tested and shown to be unmeasurable by injection; see §4. | ~~injection~~ |
| RQ5' | Does source-attributed code differ from matched non-AI code written in the *same repository* in the same period? | baseline arm (`p9_baseline`) |

RQ3 and RQ4 are what make this a software-evolution paper. They are not optional.

## 3. Provenance channels

Reported **separately**, because precision and recall differ by an order of
magnitude between them.

### AI channel

| Channel | Signal | Expected precision |
|---|---|---|
| `ai_devgpt` | DevGPT conversation→commit links (MSR 2024, CC-BY-4.0) | highest precision, but **yield for Java is 1 repository** — see below |
| `ai_trailer_claude` / `_copilot` / `_cursor` / `_aider` / `_devin` | machine-written commit trailers | high — the *tool* writes these, not the developer, so they are not self-report |
| ~~`ai_message_chatgpt` / `_copilot`~~ | developer prose naming a tool | **low — demoted to descriptive only**, see below |

**Measured 2026-10-28, and it removes DevGPT from the design.** In the 2024
SQLite release the `commit` table is *empty*; the links survive only in
`sharing.mentions`, which yields 697 distinct (repo, commit) pairs across 78
repositories. Enriching those with repository metadata (`p2b_enrich`) gives:

| | |
|---|---|
| candidates | 697 |
| distinct repositories | 78 |
| **from one repository** (`tisztamo/Junior`, Shell) | **575 (82%)** |
| repositories now deleted or private | 9 |
| language distribution | Shell 580, JavaScript 46, Python 23, gone 12, Java **4** |
| distinct Java repositories | **3** |
| Java-eligible after fork / archived / ≥10 stars | **1** (`Hack23/cia`) |

So DevGPT contributes **one usable Java repository**, not the 40–50 events a
language-share estimate suggests — the `pull_request` language distribution does
not transfer to the `sharing` commit links, because the commit links are
dominated by a single non-Java project. `pull_request` itself is no help either:
it records `repo_language`, but every Java row has `commit_sha` NULL.

**Consequence:** DevGPT is retained only as a citation and as a provenance
*validation* set, not as a channel. The commit-trailer channels are not
load-bearing alongside DevGPT — they are the **only** viable AI channel.

The trailer channels are the methodological addition to the original plan. They
did not exist at useful volume when DevGPT was collected, they are
machine-emitted and therefore unusually clean, and unlike DevGPT they keep
accruing. They are the main reason the AI arm of this study is feasible in 2026
and was not in 2023.

### Stack Overflow channel

| Channel | Signal | Expected precision |
|---|---|---|
| `so_code_url` | answer URL in a `.java` comment | high — established (Abdalkareem et al.) |
| `so_commit_url` | answer URL in the commit message | high |
| `so_clone` | token-level match to answer code (`p10_so_clones`) | lower, and **now mandatory** — the URL channels yielded almost nothing |

### The message channels are descriptive only

Measured on 757 `ai_message_copilot` candidates:

* **433 (57%) also carry a machine trailer**, so they are redundant with the
  high-precision channel.
* The 324 with a unique contribution are dominated by false positives:
  `Merge pull request #4554 from 1c-syntax/copilot/fix-build-deploy-job` (the
  word is in a *branch name*), `Bump jackson.version from 3.0.4 to 3.1.1`
  (dependabot), and `apply CodeRabbit + Copilot PR review fixes` — which is
  **inverted provenance**: a human fixing what a bot flagged, not code a bot
  wrote.

These contribute **zero** located introduction events, because a merge commit
has no authored diff of its own (`git show --unified=0` prints nothing for a
merge). That filter was previously implicit; `p3_clone_locate` now detects
merges explicitly and reports rejected candidates by reason, so the channels'
real yield is stated rather than inferred.

The channels are kept as a descriptive statistic — how often developers mention
a tool in prose is itself worth reporting — but they do not feed the analysis.

**One real channel is visible inside the noise and is left as future work.** A
merge of a `copilot/<branch>` pull request *is* genuine provenance: that branch
naming is created by the GitHub Copilot coding agent. The introduction event is
not the merge but the commits on the merged branch, reachable via second-parent
traversal. `1c-syntax/bsl-language-server` alone has hundreds of these.

### The SO arm had to be rebuilt

Measured 2026-10-28: nine Java repositories with full working trees produced
**zero** `so_code_url` hits — not one Stack Overflow answer URL in a `.java`
comment, including across commons-lang's 630 Java files — and `so_commit_url`
gave one eligible event. Developers copy Stack Overflow code *without citing
it*, which is precisely why the citation channels fail. Clone detection against
the 500-answer snippet corpus (`p10_so_clones`) is therefore the SO arm, not an
optional supplement.

Scaling the corpus does recover some citation signal: once discovery passed ~120
repositories, `so_commit_url` began appearing (validator/validator ×3,
GIScience/openrouteservice ×2, jplag, yaxim, hn-android,
dustin/java-memcached-client). Both channels are kept and reported separately.

**Two precision traps found on the first clone-detection run**, both of which
would have corrupted the SO arm silently:

1. **Decompiled and generated sources.** 103 of 107 matches came from one
   repository's `decompiled/java/` tree — 303,877 files of decompiled bytecode,
   which is boilerplate-dense and matches generic k-grams readily. All false
   positives. `p10` now excludes decompiled and generated trees by path.
2. **Snippets too short to attribute.** The survivors matched snippets of only
   15–33 k-grams (~20 tokens). `list.add(x); for (int i = 0; ...` appears in
   thousands of files; attributing it to one answer is noise. `p10` now requires
   ≥30 k-grams in the snippet and ≥25 shared k-grams in absolute terms, on top
   of 0.8 containment.

After both fixes the stage returns zero matches on the nine promoted
repositories, which is the honest answer at that corpus size rather than a
hundred false ones.

### Mandatory validation

≥100 attributions per channel, two independent raters, blind, Cohen's κ reported,
per-channel precision with Wilson intervals. `p7_validate.py` generates the
blinded sheets and scores them. Without this the study is unfalsifiable and the
numbers downstream mean nothing.

## 4. Injection sub-study (RQ5) — tested and dropped

Paired, within-host. For each question in the snippet corpus both the Stack
Overflow and the ChatGPT answer are integrated into the *same* host project at
the *same* site, so the host is its own control:

```
outcome = measure(host + variant) − measure(host at HEAD)
```

This is the only part of the design where source assignment is randomised, so it
is the only part that supports a causal claim. It is also where repository-wide
architecture smells become measurable.

**This limitation was tested, and it is fatal to RQ5 as specified.** Measured
2026-10-28 on 2 hosts x 3 snippet pairs (apache/commons-lang, google/gson):

* **6 of 6 snippet-variants produced identical deltas across both hosts.** The
  host makes no difference whatsoever, which is the opposite of what a study of
  architectural *fit* must find.
* The only architecture-level change was `Unutilized Abstraction` **+1**, on
  every single injection including both arms. That is a measurement artifact:
  a newly added class that nothing references is by definition unutilised. It
  says nothing about the snippet.
* Everything else in the deltas (`Magic Number +8`, `Empty catch clause +1`) is
  **snippet-intrinsic** - precisely what the snippet study already measured.

The reason is structural, not a tuning problem. A new compilation unit has no
architectural relationship to its host: nothing calls it, it calls nothing, so
no coupling, cyclic-dependency or hub-like smell can arise. Injecting into a
repository does not make a measurement repository-level.

**Decision: RQ5 is dropped, and the negative result is reported instead.** It is
a genuine methodological contribution and it answers the editor's suggestion
directly: integrating snippets into real projects does *not* by itself reveal
architectural debt, so the repository-level signal has to come from code that
was really written into a project and really maintained there - the mining arm
(SS3) and the matched within-repository baseline (SS4a). Those stand on their
own.

Reviving RQ5 would require transplanting snippets into the bodies of *existing*
methods so call sites and coupling are exercised. That cannot be automated at
corpus scale - the result almost never compiles - so it is out of scope.

**What injection cannot do, and why it is not the primary design:** code that was
injected has no subsequent history. "Track the next 5–10 commits" is undefined
for it — the only commits that could follow are ones we wrote ourselves. Survival
time, real remediation and defect linkage require code that genuinely entered a
repository and was genuinely maintained afterwards. Hence §3.

## 5. Measurement

| Tool | Gives | Attribution granularity | Role |
|---|---|---|---|
| SonarQube (SQALE) | severity Blocker…Info, remediation effort in minutes | **line** — "this issue is on a line the commit added" | carries RQ2, the central switch from the snippet study |
| DesigniteJava | implementation / design smells (architecture-level smells live among the design smells), per-method CC | **file** — "this smell is in a file the commit touched" | repository-wide architectural context; continuity with the snippet study's indicators |

The two granularities are **not interchangeable and are never pooled**.
DesigniteJava reports at class/method level with no line numbers, so a
per-event Designite count includes smells that were already in the file. Every
per-event severity and cost claim comes from SonarQube; Designite carries the
repository-wide context and the snippet-study comparison.

Sampling points per event: the **introduction commit** and the **matched
observation horizon** (introduction + 365 days, or HEAD if sooner).

**Test code is included and flagged, not excluded.** The pilot events land in
test sources as often as in main sources — AI-assisted commits in particular
carry their own tests — so excluding `**/test/**` would discard a large part of
the phenomenon. Every tracked range records `is_test`, so any analysis can be
rerun on main sources alone as a robustness check.

**SonarQube processes uploaded reports asynchronously.** Querying the issues API
when the scanner exits returns an empty result and silently produces zeros. The
pipeline blocks on the Compute Engine task id from `report-task.txt`. Worth
stating because it is a trap that would corrupt every RQ2 number without
raising an error.

## 6. The two threats that must be designed for, not discussed

**Asymmetric exposure.** AI-attributed code cannot predate 2023; SO-attributed
code goes back a decade. A naive survival comparison concludes that AI code is
remediated faster purely because it has been watched for less time. Handled in
the data: every range is censored at `min(observed, OBSERVATION_WINDOW_DAYS)`,
and events outside the window are coded as censored, not as failures
(`p5_track.py`). This is the single most likely reason a reviewer rejects a
design like this one.

**Non-random assignment.** Repositories that adopt AI tooling differ
systematically — newer, different domains, different team practices. Source is
not randomly assigned in the mining arm, so raw comparisons are confounded.
Handled by the Cox and negative-binomial models adjusting for stars, project
age and change size, and by the injection arm, where assignment *is* randomised.

Also documented: survivorship bias (deleted repositories are invisible);
training-data contamination (Stack Overflow answers are almost certainly in the
model's training set, so the two sources are not independent); detector precision
on real code; SonarJava's reduced rule set without compiled bytecode (§7).

## 7. Known measurement compromise

SonarJava without `sonar.java.binaries` runs a reduced rule set. Building every
repository at every sampled commit is not tractable. Analysis is therefore
source-only and applied **identically to both channels**: it biases the absolute
debt level, not the between-channel comparison. Stated in the methodology.

## 8. Analysis

| RQ | Method |
|---|---|
| RQ1 | paired proportions, candidate state vs. post-adoption state |
| RQ2 | severity distributions; remediation minutes; bootstrap CIs; Mann-Whitney with Cliff's δ |
| RQ3 | Kaplan–Meier; log-rank; Cox proportional hazards with source as the covariate of interest |
| RQ4 | negative binomial regression, log exposure as offset; SZZ-lite fix linkage |
| RQ5 | paired within-host differences; Wilcoxon signed-rank |

## 8a. Corpus construction — measured, and it inverts the mining strategy

The commit-trailer signal is enormous. Measured against GitHub commit search on
2026-10-28:

| trailer query | matching commits |
|---|---|
| `"Co-Authored-By: Claude"` | 22,360,758 |
| `"Co-authored-by: Cursor"` | 3,655,166 |
| `"Co-authored-by: Copilot"` | 3,074,232 |
| `"aider: "` | 871,521 |
| `"Co-Authored-By: Devin"` | 591,002 |

**But not one of 500 sampled commits came from a Java repository.** The samples
are dominated by TypeScript, Python, Rust and C++. GitHub's commit search
supports no `language:` qualifier, so Java commits cannot be asked for, and
sampling globally will essentially never return them.

Repository search *does* support `language:`. The corpus is therefore built the
other way round, and this ordering is not an optimisation but a requirement:

1. `p0_sample_corpus` — sample Java repositories by `language:java`, stratified
   over six disjoint star bands (each query is capped at 1000 results, and one
   query would otherwise return only the famous projects). The population is
   ~45,000 repositories pushed since 2023-06.
2. `p1b_local_scan --blobless` — clone commit history *without file contents*
   (`--filter=blob:none --no-checkout`), run `git log --grep` per channel, and
   delete immediately any repository with no signal. A full-history corpus of
   360 repositories projects to ~35 GB against 61 GB free; blobless discovery
   makes a corpus-wide pass affordable and is the only reason this scales.
3. Promote only the keepers to full clones, then measure.

This path also has strictly higher recall than commit search: `git log --grep`
sees every commit in a repository, whereas commit search caps at 1000 results
across all of GitHub.

## 9. Pipeline

```
p0_sample_corpus      Java repositories by star band   → java_corpus.jsonl
p1_provenance_probe   GitHub search per channel        → candidates_*.jsonl
p1b_local_scan        clone scan; --blobless discovery → candidates_*.jsonl
p1c_promote           blobless keepers -> full clones
p2_devgpt_extract     DevGPT SQLite → (repo, sha)      → candidates_ai_devgpt.jsonl
p2b_enrich            repo language/stars/age via API  → candidates_*.jsonl annotated
p3_clone_locate       clone; pin the introduction event → introduction_events.jsonl
p4_measure            SonarQube + DesigniteJava        → measurements.jsonl
p5_track              git blame --reverse; censoring    → survival_rows.jsonl
p6_analyze            RQ2/RQ3/RQ4                      → data/final/
p7_validate           blinded rater sheets; κ          → data/final/validation/
p8_injection          RQ5 within-host experiment       → injection_results.jsonl
p9_baseline           matched within-repo non-AI arm    → introduction_events.jsonl
p10_so_clones         SO clone detection (SO arm)       → candidates_so_clone.jsonl
```

## 9a. Pilot result, 2026-10-28

Run on 3 repositories (apache/commons-lang, square/okhttp, google/gson) to
validate the machinery end to end, not to produce findings.

* **10 introduction events located**, 82 tracked line ranges, all eligible.
* Channel yield: `ai_trailer_copilot` 6, `so_commit_url` 3, `ai_trailer_codex` 1,
  `ai_message_chatgpt` 1. `so_code_url` found **nothing** — none of the three
  repositories cites a Stack Overflow answer in a `.java` comment. If that holds
  at scale, the SO arm must come from commit messages plus clone detection, and
  the code-comment channel is not viable on its own.
* **The asymmetric-exposure threat is real and large.** AI-attributed events in
  this corpus have 7–26 days of post-introduction history; SO-attributed events
  have 4,250–5,906 days. Two to three orders of magnitude. Any uncensored
  comparison of these two arms is meaningless, which is why censoring is in the
  data rather than the discussion.
* SonarQube measures what it needs to: 196 issues / 2,622 minutes of SQALE
  remediation effort repository-wide at one introduction commit. Issue-to-line
  attribution works; it returned zero issues *inside* the introduced ranges for
  the first two events, which is a genuine result for a mature, clean codebase,
  not a defect.
* Cox and the negative-binomial model correctly refuse to fit at this sample
  size and say why. They need ≥10 events per source arm and ≥10 repositories.

**Implication for sequencing:** the binding constraint is not tooling, it is
corpus size. The machinery is built and verified; what it needs is repositories.

## 10. Go/no-go gate

Pilot on ~20 repositories per channel and measure attribution precision
**before** scaling. If precision on the URL and trailer channels is below ~80%,
the design needs rethinking, and learning that now is far cheaper than learning
it after the corpus is built. Do not skip this.

Target corpus after the gate: 200–300 repositories, or a fixed count of
introduction events per channel, whichever binds first.


---

## 11. Executed results, 2026-10-28

The protocol above was executed. Final corpus and headline numbers:

| | |
|---|---|
| Java repositories sampled | 360 (6 star bands x 60) |
| Keepers (≥1 provenance signal) | 134 (37%) |
| `ai_trailer_claude` / `copilot` / `cursor` / `codex` / `devin` | 5,735 / 772 / 164 / 19 / 9 |
| `so_commit_url` | 141 |
| Repos with AI trailer / SO citation / **both** | 107 / 49 / **31** |
| Repositories promoted and measured | 18 |
| Introduction events located (eligible) | 320 (313) |
| Matched controls (eligible) | 576 (567), median era gap 1 day |
| Tracked line ranges | 28,374 over 877 events |
| Disturbed in window / right-censored | 3,117 / 25,257 |

### The clustering correction is the single most important result

One introduction commit contributes a mean of 32.8 line ranges and a maximum of
6,604. Those rows share a commit, an author and a review, so they are not
independent. Clustering standard errors on the introduction commit changes the
inference completely while leaving every point estimate untouched:

| Model | Naive *p* | Clustered *p* |
|---|---|---|
| Cox, AI vs. baseline (HR 0.27) | 7e-183 | **0.013** |
| Cox, AI vs. SO (HR 0.42) | 3e-11 | **0.078** |
| Negative binomial, file touches (IRR 0.86) | 2e-20 | **0.319** |
| Cox, test-file covariate (HR 0.77) | 5e-7 | **0.247** |

The event-level sensitivity analysis (one row per commit, 877 events) finds **no
difference at all**: 35.3% of AI events disturbed in window vs. 37.2% of
baseline (Fisher OR 0.92, p=0.599), file touches Mann-Whitney p=0.306, Cliff's
δ = −0.04. Because the event-level and clustered line-level results disagree,
the line-level estimate is being driven by a handful of very large commits, and
the honest conclusion is **no reliable difference**.

**How to apply:** never report the line-level model without clustering, and
always report the event-level check beside it.

### RQ status after execution

* **RQ1** — not answered. Requires clone-detection matches linking a specific
  answer to specific adopted lines; at justified thresholds there were none in
  the measured subset.
* **RQ2** — open. The instrument works (196 issues / 2,622 min repository-wide at
  one commit), but only 1 of 14 measured events carried any SQALE issue *on its
  introduced lines* (one Critical, 30 min). A cost distribution per source needs
  far more events.
* **RQ3** — no reliable difference (see above).
* **RQ4** — null after clustering.
* **RQ5** — dropped; injection cannot measure architectural fit (§4).

### Environment notes for replication

* TeX Live 2026 no longer ships `etex.sty`, whose `\reserveinserts` the Wiley
  class calls at line 356. The manuscript prepends
  `\providecommand\reserveinserts[1]{}` before `\documentclass`.
* The template bundles `listings.sty` v1.8d, which modern `lstmisc.sty` rejects.
  The manuscript omits the `listings` package and builds in `build/`, a
  directory holding the template files *except* that one.
* The Wiley class requires XeTeX; `pdflatex` fails on `fontspec`.
