# Environment the results were produced in

| Component | Version |
|---|---|
| Python | 3.12.3 |
| Java (for the analysers) | OpenJDK 21.0.12.1 |
| SonarQube | 26.10.0.133078 (Community Edition) |
| sonar-scanner CLI | 7.0.2.4839 |
| DesigniteJava | bundled jar, community edition |
| OS | Linux x86-64 |

Python package versions are pinned in `requirements.txt`.

## Reproducing without the heavy tooling

The analysis stages (`p6_analyze`, `p7b_screen`, `make_figures`) read only the
committed data and need **none** of Java, SonarQube or DesigniteJava, and no
network access. They reproduce every statistic and figure in the paper.

The mining stages (`p0`–`p5`, `p9`, `p10`) re-derive that data from GitHub and
additionally require a GitHub token, a SonarQube instance, and tens of
gigabytes of disk for full-history clones. `docs/RUNBOOK.md` gives the full
sequence and the disk-management strategy.

## Analyser caveats that affect the numbers

- **SonarJava runs source-only.** Building every repository at every sampled
  commit is not tractable, so `sonar.java.binaries` is unset and a reduced rule
  set applies. This depresses absolute debt levels equally across all arms; it
  does not bias the between-arm comparison.
- **DesigniteJava reports at class/method granularity with no line numbers**, so
  its attribution is file-scoped while SonarQube's is line-scoped. The two are
  never pooled. Per-event cost claims come from SonarQube only.
