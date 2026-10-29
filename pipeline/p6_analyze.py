#!/usr/bin/env python3
"""
Stage 6 - the analyses the four research questions need.

RQ1 adoption   paired proportions: debt present in the source snippet vs. debt
               surviving into the repository.
RQ2 cost       severity distribution and SQALE remediation minutes per source,
               with bootstrap CIs (the distributions are heavily skewed, so
               report medians and bootstrap, not t-tests).
RQ3 evolution  Kaplan-Meier survival of introduced lines, log-rank test, and a
               Cox model with source as the covariate of interest, adjusted for
               the confounders that are not randomly assigned.
RQ4 process    change-proneness and fix-proneness via negative binomial
               regression with exposure as the offset.

Reports go to data/final/ as CSV plus a text summary ready to be turned into
manuscript tables.

Usage:
    python -m pipeline.p6_analyze
"""
from __future__ import annotations
import json, sys
import numpy as np
import pandas as pd
from .config import INTERIM, FINAL

RNG = np.random.default_rng(20261028)

# A single introduction commit contributes one row per introduced line range -
# in this corpus a mean of 32 and a maximum of 6,604. Those rows are not
# independent observations: they share a commit, an author and a review. Treated
# as independent they inflate the effective sample size from ~900 events to
# ~28,000 rows and drive p-values to absurd magnitudes. Every model below
# therefore clusters its standard errors on the introduction commit, and each
# headline result is repeated at event level as a sensitivity check.
CLUSTER = "intro_sha"


def load(name: str) -> pd.DataFrame:
    f = INTERIM / name
    if not f.exists():
        return pd.DataFrame()
    return pd.DataFrame([json.loads(l) for l in f.read_text().splitlines() if l.strip()])


def boot_ci(x, stat=np.median, n=10000, conf=0.95):
    x = np.asarray([v for v in x if v is not None and not np.isnan(v)], dtype=float)
    if len(x) < 2:
        return (np.nan, np.nan, np.nan)
    draws = stat(RNG.choice(x, (n, len(x)), replace=True), axis=1)
    lo, hi = np.percentile(draws, [(1 - conf) / 2 * 100, (1 + conf) / 2 * 100])
    return float(stat(x)), float(lo), float(hi)


# ------------------------------------------------------------------ RQ2: cost

def rq2_cost(meas: pd.DataFrame, out: list[str]):
    if meas.empty:
        out.append("RQ2: no measurements yet.\n")
        return pd.DataFrame()
    rows = []
    for _, r in meas.iterrows():
        ev, intro = r["event"], r.get("intro") or {}
        s = (intro.get("sonar") or {}).get("introduced")
        if not s:
            continue
        rows.append({
            "source": ev["source"], "channel": ev["channel"], "repo": ev["repo"],
            "intro_sha": ev["intro_sha"], "added_lines": ev["added_lines"],
            "n_issues": s["n"], "effort_min": s["effort_min"],
            "effort_per_100_loc": 100 * s["effort_min"] / max(ev["added_lines"], 1),
            **{f"sev_{k.lower()}": v for k, v in s["by_severity"].items()},
        })
    df = pd.DataFrame(rows)
    if df.empty:
        out.append("RQ2: no Sonar issues attributed to introduced lines yet.\n")
        return df

    out.append("RQ2 - severity and remediation cost by source\n")
    for src, g in df.groupby("source"):
        med, lo, hi = boot_ci(g["effort_min"])
        mper, plo, phi = boot_ci(g["effort_per_100_loc"])
        out.append(f"  {src}: n={len(g)} events, "
                   f"median remediation {med:.0f} min (95% CI {lo:.0f}-{hi:.0f}), "
                   f"per 100 LOC {mper:.1f} (95% CI {plo:.1f}-{phi:.1f})")
    sev_cols = [c for c in df.columns if c.startswith("sev_")]
    if sev_cols:
        out.append("\n  severity counts:")
        out.append(df.groupby("source")[sev_cols].sum().to_string())
    from scipy import stats
    groups = [g["effort_min"].to_numpy() for _, g in df.groupby("source")]
    if len(groups) == 2 and all(len(g) > 1 for g in groups):
        u = stats.mannwhitneyu(*groups, alternative="two-sided")
        n1, n2 = map(len, groups)
        delta = 2 * u.statistic / (n1 * n2) - 1     # Cliff's delta
        out.append(f"\n  Mann-Whitney U p={u.pvalue:.4g}, Cliff's delta={delta:+.3f}")
    out.append("")
    return df


# -------------------------------------------------------------- RQ3: evolution

def rq3_survival(surv: pd.DataFrame, out: list[str]):
    if surv.empty:
        out.append("RQ3: no survival rows yet.\n")
        return
    out.append("RQ3 - survival of introduced code\n")
    out.append(f"  {len(surv)} line ranges, "
               f"{int(surv['event_observed'].sum())} changed inside the window, "
               f"{int((1 - surv['event_observed']).sum())} right-censored")
    for src, g in surv.groupby("source"):
        out.append(f"    {src:9s} n={len(g):>5d} events={int(g['event_observed'].sum()):>4d} "
                   f"repos={g['repo'].nunique():>3d} "
                   f"median exposure {g['observed_days'].median():>6.0f}d "
                   f"alive at HEAD {100 * g['frac_surviving'].mean():>5.1f}%")

    try:
        from lifelines import KaplanMeierFitter, CoxPHFitter
        from lifelines.statistics import logrank_test
    except ImportError:
        out.append("  (install lifelines for KM/Cox)\n")
        return

    kmf, curves = KaplanMeierFitter(), {}
    for src, g in surv.groupby("source"):
        kmf.fit(g["duration_days"], g["event_observed"], label=src)
        curves[src] = kmf.survival_function_.copy()
        out.append(f"  KM median time-to-first-change, {src}: "
                   f"{kmf.median_survival_time_}")
    if curves:
        pd.concat(curves.values(), axis=1).to_csv(FINAL / "rq3_km_curves.csv")

    # The primary comparison is AI vs the matched within-repository baseline.
    # Stack Overflow is reported separately: the pilot shows it is far too
    # underpowered to carry a hazard model, and pooling it would hide that.
    pair = surv[surv["source"].isin(["ai", "baseline"])]
    if pair["source"].nunique() == 2:
        a = pair[pair["source"] == "ai"]
        b = pair[pair["source"] == "baseline"]
        lr = logrank_test(a["duration_days"], b["duration_days"],
                          a["event_observed"], b["event_observed"])
        out.append(f"\n  PRIMARY  log-rank ai vs baseline: p={lr.p_value:.4g}")

        cox = pair.assign(
            is_ai=(pair["source"] == "ai").astype(int),
            log_lines=np.log1p(pair["n_lines"]),
            is_test=pair["is_test"].astype(int),
        )[["duration_days", "event_observed", "is_ai", "log_lines",
           "is_test", "repo", CLUSTER]].dropna()

        out.append(f"  {cox[CLUSTER].nunique()} introduction commits contribute "
                   f"{len(cox)} line ranges "
                   f"(mean {len(cox) / max(cox[CLUSTER].nunique(), 1):.1f} per commit)")
        dropped = [c for c in ("log_lines", "is_test") if cox[c].nunique() < 2]
        if dropped:
            cox = cox.drop(columns=dropped)
            out.append(f"  dropped zero-variance covariates: {dropped}")

        events_per_arm = pair.groupby("source")["event_observed"].sum()
        if len(cox) < 30 or events_per_arm.min() < 10:
            out.append(f"  Cox skipped: needs >=30 rows and >=10 events per arm; "
                       f"have {len(cox)} rows, events {events_per_arm.to_dict()}.")
        else:
            # Stratify by repository. Treatment and control are matched *within*
            # project, so the baseline hazard belongs to the project, not to the
            # pooled corpus - stratification is what makes the matching count
            # rather than merely adjusting for it.
            cph = CoxPHFitter()
            try:
                cph.fit(cox, "duration_days", "event_observed", strata=["repo"],
                        cluster_col=CLUSTER)
                out.append("\n  Cox proportional hazards, stratified by "
                           "repository, SEs clustered on the introduction commit:")
                out.append(cph.summary[["coef", "exp(coef)",
                                        "exp(coef) lower 95%",
                                        "exp(coef) upper 95%", "p"]].to_string())
                cph.summary.to_csv(FINAL / "rq3_cox_stratified.csv")
                hr = cph.summary.loc["is_ai", "exp(coef)"]
                pv = cph.summary.loc["is_ai", "p"]
                out.append(f"\n  AI-attributed lines are touched at {hr:.2f}x the "
                           f"hazard of matched non-AI lines in the same "
                           f"repository (p={pv:.4g}).")
                out.append("  A hazard above 1 means earlier disturbance, which "
                           "can be remediation or churn - RQ4 separates them.")
            except Exception as e:
                out.append(f"  Cox failed: {e}")

    # The editor's question is AI vs crowdsourced. Comparing the two across
    # different repositories is confounded - projects that adopt AI tooling
    # differ systematically from those that cite Stack Overflow. Restricting to
    # repositories that contain BOTH kinds of event removes that confounding
    # entirely, so this is the defensible form of the headline comparison.
    has_ai = set(surv.loc[surv["source"] == "ai", "repo"])
    has_so = set(surv.loc[surv["source"] == "so", "repo"])
    dual = sorted(has_ai & has_so)
    out.append(f"\n  WITHIN-REPOSITORY AI vs Stack Overflow")
    if not dual:
        out.append(f"    no repository yet holds both an AI and an SO event "
                   f"({len(has_ai)} repos with AI, {len(has_so)} with SO). "
                   f"This is the comparison the editor asked for, so it is the "
                   f"corpus's binding constraint.")
    else:
        d = surv[(surv["repo"].isin(dual)) & (surv["source"].isin(["ai", "so"]))]
        out.append(f"    {len(dual)} dual-arm repositories, {len(d)} ranges "
                   f"(ai={int((d['source'] == 'ai').sum())}, "
                   f"so={int((d['source'] == 'so').sum())})")
        for src, g in d.groupby("source"):
            out.append(f"      {src:3s} events={int(g['event_observed'].sum()):>4d} "
                       f"alive at HEAD {100 * g['frac_surviving'].mean():>5.1f}% "
                       f"mean file touches {g['file_touches'].mean():>5.2f}")
        ev_per = d.groupby("source")["event_observed"].sum()
        if ev_per.min() >= 10 and len(d) >= 30:
            dd = d.assign(is_ai=(d["source"] == "ai").astype(int),
                          log_lines=np.log1p(d["n_lines"]))
            cols = ["duration_days", "event_observed", "is_ai", "repo", CLUSTER]
            if dd["log_lines"].nunique() > 1:
                cols.insert(3, "log_lines")
            try:
                cph = CoxPHFitter()
                cph.fit(dd[cols].dropna(), "duration_days", "event_observed",
                        strata=["repo"], cluster_col=CLUSTER)
                out.append("\n    Cox, stratified by repository, clustered on "
                           "the introduction commit (AI vs SO):")
                out.append(cph.summary[["exp(coef)", "exp(coef) lower 95%",
                                        "exp(coef) upper 95%", "p"]].to_string())
                cph.summary.to_csv(FINAL / "rq3_cox_ai_vs_so_within_repo.csv")
            except Exception as e:
                out.append(f"    Cox failed: {e}")
        else:
            out.append(f"    not powered yet: needs >=10 events per arm, "
                       f"have {ev_per.to_dict()}")
        d.to_csv(FINAL / "dual_arm_rows.csv", index=False)

    so = surv[surv["source"] == "so"]
    if len(so):
        out.append(f"\n  Stack Overflow arm: {len(so)} ranges, "
                   f"{int(so['event_observed'].sum())} events, "
                   f"{so['repo'].nunique()} repositories - reported for "
                   f"completeness, not powered for inference.")
    out.append("")


# ----------------------------------------------------------------- RQ4: process

def event_level(surv: pd.DataFrame, out: list[str]):
    """One row per introduction commit, as a sensitivity check.

    Collapsing to the event removes the within-commit correlation entirely
    rather than merely correcting for it, at the cost of discarding line-level
    resolution. If the event-level and clustered line-level results disagree,
    the line-level one is being driven by a handful of very large commits.
    """
    if surv.empty:
        return
    g = surv.groupby([CLUSTER, "source", "repo"], as_index=False).agg(
        n_ranges=("n_lines", "size"),
        n_lines=("n_lines", "sum"),
        frac_surviving=("frac_surviving", "mean"),
        any_changed=("event_observed", "max"),
        duration_days=("duration_days", "min"),
        window_days=("window_days", "first"),
        observed_days=("observed_days", "first"),
        file_touches=("file_touches", "max"),
        fix_touches=("fix_touches", "max"),
    )
    out.append("EVENT-LEVEL sensitivity analysis (one row per introduction commit)\n")
    out.append(f"  {len(g)} events across {g['repo'].nunique()} repositories")
    for src, grp in g.groupby("source"):
        out.append(f"    {src:9s} n={len(grp):>4d} "
                   f"changed-in-window {100 * grp['any_changed'].mean():>5.1f}% "
                   f"median exposure {grp['observed_days'].median():>6.0f}d "
                   f"lines alive {100 * grp['frac_surviving'].mean():>5.1f}% "
                   f"mean file touches {grp['file_touches'].mean():>5.2f}")

    pair = g[g["source"].isin(["ai", "baseline"])]
    if pair["source"].nunique() == 2:
        from scipy import stats
        a = pair.loc[pair["source"] == "ai", "any_changed"]
        b = pair.loc[pair["source"] == "baseline", "any_changed"]
        tbl = [[int(a.sum()), int((1 - a).sum())],
               [int(b.sum()), int((1 - b).sum())]]
        orr, pv = stats.fisher_exact(tbl)
        out.append(f"\n  UNPAIRED, exposure-unadjusted (reported for completeness only):")
        out.append(f"    any disturbance in window: Fisher exact OR={orr:.3f}, p={pv:.4g}")
        ta = pair.loc[pair["source"] == "ai", "file_touches"]
        tb = pair.loc[pair["source"] == "baseline", "file_touches"]
        u = stats.mannwhitneyu(ta, tb, alternative="two-sided")
        delta = 2 * u.statistic / (len(ta) * len(tb)) - 1
        out.append(f"    file touches: Mann-Whitney p={u.pvalue:.4g}, "
                   f"Cliff's delta={delta:+.3f}")

        # Those two tests ignore exposure, and exposure is NOT balanced: controls
        # carry a mean of 7.0 days more post-introduction history than the events
        # they are matched to (Wilcoxon p=6.7e-10), because the matcher minimises
        # |date difference| without constraining its direction. More days at risk
        # means more opportunity to be disturbed, which biases both tests towards
        # finding AI code more durable. The paired analysis below removes it.
        events = {}
        for _, r in g.iterrows():
            events[r[CLUSTER]] = r
        links = []
        f = INTERIM / "introduction_events.jsonl"
        if f.exists():
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                if e.get("source") == "baseline" and e.get("matched_to"):
                    links.append((e["intro_sha"], e["matched_to"]))
        rows_p = []
        for c_sha, t_sha in links:
            c, t = events.get(c_sha), events.get(t_sha)
            if c is None or t is None:
                continue
            rows_p.append({"d_exposure": c["observed_days"] - t["observed_days"],
                           "ctrl_changed": int(c["any_changed"]),
                           "ai_changed": int(t["any_changed"]),
                           "ctrl_touch": c["file_touches"],
                           "ai_touch": t["file_touches"]})
        pf = pd.DataFrame(rows_p)
        out.append(f"\n  PAIRED analysis over {len(pf)} matched pairs:")
        if len(pf):
            w = stats.wilcoxon(pf["ctrl_changed"] * 0 + pf["d_exposure"]) \
                if pf["d_exposure"].abs().sum() else None
            out.append(f"    exposure imbalance (control - AI): "
                       f"mean {pf['d_exposure'].mean():+.1f} d, "
                       f"median {pf['d_exposure'].median():+.0f} d")
            for tol in (0, 7, 30):
                sub = pf[pf["d_exposure"].abs() <= tol]
                if len(sub) < 20:
                    out.append(f"    |exposure diff| <= {tol:>2d} d: only "
                               f"{len(sub)} pairs, skipped")
                    continue
                # McNemar on the discordant pairs: the paired analogue of Fisher,
                # and the correct test for a matched binary outcome.
                b_ = int(((sub["ai_changed"] == 1) & (sub["ctrl_changed"] == 0)).sum())
                c_ = int(((sub["ai_changed"] == 0) & (sub["ctrl_changed"] == 1)).sum())
                pv2 = stats.binomtest(b_, b_ + c_, 0.5).pvalue if (b_ + c_) else 1.0
                orp = (b_ / c_) if c_ else float("inf")
                out.append(f"    |exposure diff| <= {tol:>2d} d: n={len(sub):>4d} pairs, "
                           f"AI-only disturbed={b_}, control-only={c_}, "
                           f"paired OR={orp:.2f}, McNemar p={pv2:.4g}")
        else:
            out.append("    no matched pairs with both arms tracked")
    g.to_csv(FINAL / "event_level_table.csv", index=False)
    out.append("")


def rq4_process(surv: pd.DataFrame, out: list[str]):
    if surv.empty:
        out.append("RQ4: no survival rows yet.\n")
        return
    out.append("RQ4 - change- and fix-proneness\n")
    for src, g in surv.groupby("source"):
        m, lo, hi = boot_ci(g["file_touches"], stat=np.mean)
        f, flo, fhi = boot_ci(g["fix_touches"], stat=np.mean)
        out.append(f"  {src}: mean in-window file touches {m:.2f} (95% CI {lo:.2f}-{hi:.2f}), "
                   f"mean fix-touches {f:.2f} (95% CI {flo:.2f}-{fhi:.2f})")
    try:
        import statsmodels.api as sm
        import statsmodels.formula.api as smf
        d = surv.assign(
            is_ai=(surv["source"] == "ai").astype(int),
            log_stars=np.log1p(pd.to_numeric(surv["stars"], errors="coerce").fillna(0)),
            log_exposure=np.log(surv["window_days"].clip(lower=1)),
            log_lines=np.log1p(surv["n_lines"]),
        )
        # Restrict to the matched arms and absorb the project with fixed
        # effects, so the coefficient is a within-repository contrast rather
        # than a comparison across projects of different sizes and cultures.
        d = d[d["source"].isin(["ai", "baseline"])]
        n_repos = d["repo"].nunique()
        if len(d) < 30:
            out.append(f"  NB regression skipped: needs >=30 rows, have {len(d)}.")
        else:
            terms = ["is_ai"] + [c for c in ("log_lines",) if d[c].nunique() >= 2]
            formula = "file_touches ~ " + " + ".join(terms)
            if 2 <= n_repos <= 40:
                formula += " + C(repo)"      # repository fixed effects
            try:
                m = smf.glm(formula, data=d,
                            family=sm.families.NegativeBinomial(alpha=1.0),
                            offset=d["log_exposure"]).fit(
                    cov_type="cluster",
                    cov_kwds={"groups": d[CLUSTER].to_numpy()})
                tbl = m.summary2().tables[1]
                out.append(f"\n  negative binomial, file touches "
                           f"(offset = log exposure, {n_repos} repositories"
                           f"{', fixed effects' if 'C(repo)' in formula else ''}"
                           f", SEs clustered on the introduction commit):")
                out.append(tbl.loc[[i for i in tbl.index
                                    if not i.startswith("C(repo)")]].to_string())
                tbl.to_csv(FINAL / "rq4_nb_file_touches.csv")
                if "is_ai" in tbl.index:
                    irr = np.exp(tbl.loc["is_ai", "Coef."])
                    out.append(f"\n  incidence rate ratio for AI attribution: "
                               f"{irr:.2f} (p={tbl.loc['is_ai', 'P>|z|']:.4g})")
            except Exception as e:
                out.append(f"  NB regression failed: {e}")
    except Exception as e:
        out.append(f"  NB regression failed: {e}")
    out.append("")


def main():
    meas, surv = load("measurements.jsonl"), load("survival_rows.jsonl")
    out = ["Repository-level technical debt - analysis report", "=" * 55, ""]
    cost = rq2_cost(meas, out)
    rq3_survival(surv, out)
    rq4_process(surv, out)
    event_level(surv, out)

    if not cost.empty:
        cost.to_csv(FINAL / "rq2_cost.csv", index=False)
    if not surv.empty:
        surv.to_csv(FINAL / "survival_table.csv", index=False)

    report = "\n".join(out)
    (FINAL / "analysis_report.txt").write_text(report)
    print(report)
    print(f"-> {FINAL}")


if __name__ == "__main__":
    sys.exit(main())
