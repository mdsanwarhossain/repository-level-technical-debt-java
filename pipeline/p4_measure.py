#!/usr/bin/env python3
"""
Stage 4 - measure debt at the introduction commit and at the observation horizon.

Two analysers, deliberately:

  SonarQube (SQALE)   severity (Blocker..Info) and *remediation effort in
                      minutes* per issue. This is what the snippet study could
                      not produce and what makes the cost comparison possible.
  DesigniteJava       design and implementation smells, kept for continuity
                      with the snippet study so the two papers can be compared
                      on the same indicators.

Issues are attributed to an introduction event only when they fall inside its
line ranges. Repository-wide totals are recorded separately as the architectural
context, which is what distinguishes this from snippet-level measurement.

Threat, documented here because it must reach the paper: SonarJava without
compiled bytecode (`sonar.java.binaries`) runs a reduced rule set. Building
every repository at every sampled commit is not tractable, so analysis is
source-only and applied identically to both channels - it biases the absolute
debt level, not the between-channel comparison.

Usage:
    python -m pipeline.p4_measure --designite
    python -m pipeline.p4_measure --sonar --token <sonar-token>
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path
import requests
from .config import INTERIM, REPOS, ROOT, OBSERVATION_WINDOW_DAYS
from . import designite as dj

SONAR_HOST = os.environ.get("SONAR_HOST", "http://localhost:9000")


def _scanner() -> str:
    """The bundled sonar-scanner if present, else whatever is on PATH."""
    local = sorted((ROOT / "tools").glob("sonar-scanner-*/bin/sonar-scanner"))
    return str(local[-1]) if local else "sonar-scanner"


def sonar_token() -> str:
    f = ROOT / ".sonar_token"
    return (os.environ.get("SONAR_TOKEN", "").strip()
            or (f.read_text().strip() if f.exists() else ""))



def run(cmd, cwd=None, check=True, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} -> {p.returncode}: {p.stderr[-400:]}")
    return p.stdout


def checkout(repo: Path, sha: str):
    run(["git", "checkout", "--quiet", "--force", sha], cwd=repo)


def branch_tip(repo: Path) -> str:
    """The branch name this clone tracks, for restoring HEAD afterwards."""
    b = run(["git", "symbolic-ref", "--short", "HEAD"], cwd=repo, check=False).strip()
    if b:
        return b
    return run(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
               cwd=repo, check=False).strip().removeprefix("origin/") or "HEAD"


def horizon_commit(repo: Path, intro_sha: str, days: int) -> str:
    """Last commit within `days` of the introduction - the matched observation
    horizon that neutralises the asymmetric-exposure threat."""
    from datetime import datetime, timedelta
    intro_date = run(["git", "show", "-s", "--format=%aI", intro_sha], cwd=repo).strip()
    cutoff = datetime.fromisoformat(intro_date) + timedelta(days=days)
    log = run(["git", "log", "--format=%H %aI", f"{intro_sha}..HEAD"],
              cwd=repo, check=False)
    within = [l.split()[0] for l in log.splitlines()
              if l.strip() and datetime.fromisoformat(l.split()[1]) <= cutoff]
    return within[0] if within else intro_sha   # git log is newest-first


# ---------------------------------------------------------------- DesigniteJava

def designite(repo: Path, ev: dict) -> dict:
    """Smells repository-wide and inside the event's own files.

    The repository-wide figure is the architectural context that snippet-level
    measurement cannot see; the per-event figure is what the introduction
    actually brought in. Attribution goes through Package + Type name, because
    DesigniteJava reports no file path (see pipeline/designite.py).
    """
    try:
        result = dj.run_designite(repo)
    except (dj.DesigniteError, subprocess.TimeoutExpired, OSError) as e:
        return {"error": str(e)[:300]}
    return {
        "repo_wide": dj.counts(result),
        "in_event_files": dj.counts_for_files(result, set(ev["files"])),
    }


# -------------------------------------------------------------------- SonarQube

class Sonar:
    def __init__(self, token: str, host: str = SONAR_HOST):
        self.host, self.token = host.rstrip("/"), token
        self.s = requests.Session()
        self.s.auth = (token, "")

    def wait_up(self, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                r = self.s.get(f"{self.host}/api/system/status", timeout=10)
                if r.ok and r.json().get("status") == "UP":
                    return True
            except requests.RequestException:
                pass
            time.sleep(5)
        return False

    def scan(self, repo: Path, key: str):
        """Upload an analysis and block until the server has processed it.

        The scanner returns as soon as the report is uploaded, but SonarQube
        processes it asynchronously in its Compute Engine. Querying issues
        straight after the scanner exits returns an empty result - the
        analysis simply is not there yet. The scanner writes the task id to
        report-task.txt, so wait on that.
        """
        run([_scanner(),
             f"-Dsonar.projectKey={key}",
             "-Dsonar.sources=.",
             f"-Dsonar.host.url={self.host}",
             f"-Dsonar.token={self.token}",
             "-Dsonar.java.binaries=.",
             "-Dsonar.scm.disabled=true",
             "-Dsonar.exclusions=**/target/**,**/build/**",
             ], cwd=repo, timeout=3600)
        self._await_ce(repo)

    def _await_ce(self, repo: Path, timeout: int = 1800):
        tasks = sorted(repo.rglob("report-task.txt"),
                       key=lambda f: f.stat().st_mtime)
        if not tasks:
            raise RuntimeError("scanner wrote no report-task.txt")
        props = dict(l.split("=", 1) for l in tasks[-1].read_text().splitlines()
                     if "=" in l)
        task_id = props.get("ceTaskId")
        if not task_id:
            raise RuntimeError("report-task.txt has no ceTaskId")
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = self.s.get(f"{self.host}/api/ce/task", params={"id": task_id},
                           timeout=30)
            r.raise_for_status()
            status = r.json().get("task", {}).get("status")
            if status == "SUCCESS":
                return
            if status in ("FAILED", "CANCELED"):
                raise RuntimeError(f"compute engine task {status}")
            time.sleep(3)
        raise RuntimeError("compute engine task did not finish in time")

    def issues(self, key: str) -> list[dict]:
        """Every issue with its severity and remediation effort in minutes."""
        out, page = [], 1
        while True:
            r = self.s.get(f"{self.host}/api/issues/search", timeout=60, params={
                "components": key, "ps": 500, "p": page,
                "additionalFields": "_all", "resolved": "false",
            })
            r.raise_for_status()
            d = r.json()
            if page == 1:
                self.last_total = d.get("total", 0)
                self.last_effort = d.get("effortTotal", 0)
            for i in d.get("issues", []):
                out.append({
                    "rule": i.get("rule"),
                    "severity": i.get("severity"),
                    "type": i.get("type"),
                    "effort_min": _minutes(i.get("effort") or i.get("debt") or "0min"),
                    "component": (i.get("component") or "").split(":", 1)[-1],
                    "line": i.get("line"),
                })
            if page * 500 >= min(d.get("total", 0), 10000):
                break
            page += 1
        return out


def _minutes(s: str) -> int:
    """'1h30min' / '45min' / '2d' -> minutes. Sonar's working day is 8h."""
    import re
    total, s = 0, (s or "").strip()
    for val, unit in re.findall(r"(\d+)(d|h|min)", s):
        total += int(val) * {"d": 480, "h": 60, "min": 1}[unit]
    return total


def attribute(issues: list[dict], ev: dict) -> dict:
    """Split issues into those inside the introduced lines and the rest."""
    ranges = {p: rs for p, rs in ev["files"].items()}
    inside, outside = [], []
    for i in issues:
        rs = ranges.get(i["component"])
        if rs and i["line"] and any(a <= i["line"] <= b for a, b in rs):
            inside.append(i)
        else:
            outside.append(i)
    def agg(xs):
        by_sev = {}
        for x in xs:
            by_sev[x["severity"]] = by_sev.get(x["severity"], 0) + 1
        return {"n": len(xs), "effort_min": sum(x["effort_min"] for x in xs),
                "by_severity": by_sev}
    return {"introduced": agg(inside), "repo_rest": agg(outside),
            "introduced_issues": inside}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--designite", action="store_true")
    ap.add_argument("--sonar", action="store_true")
    ap.add_argument("--token", help="SonarQube user token "
                    "(default: $SONAR_TOKEN or .sonar_token)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--at", choices=["intro", "horizon", "both"], default="both")
    a = ap.parse_args()

    f = INTERIM / "introduction_events.jsonl"
    if not f.exists():
        sys.exit("no introduction_events.jsonl - run p3_clone_locate first")
    events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    events = [e for e in events if e["eligible"]]
    if a.limit:
        events = events[: a.limit]
    print(f"{len(events)} eligible events")

    sonar = None
    if a.sonar:
        tok = a.token or sonar_token()
        if not tok:
            sys.exit("--sonar needs a token: --token, $SONAR_TOKEN or .sonar_token")
        sonar = Sonar(tok)
        if not sonar.wait_up():
            sys.exit(f"SonarQube at {SONAR_HOST} did not come up")

    out_path = INTERIM / "measurements.jsonl"
    with out_path.open("a") as out:
        for n, ev in enumerate(events, 1):
            repo = REPOS / ev["repo"].replace("/", "__")
            if not (repo / ".git").exists():
                continue
            points = {"intro": ev["intro_sha"]}
            if a.at in ("horizon", "both"):
                points["horizon"] = horizon_commit(repo, ev["intro_sha"],
                                                   OBSERVATION_WINDOW_DAYS)
            print(f"[{n}/{len(events)}] {ev['repo']} {ev['intro_sha'][:8]} "
                  f"({ev['source']})")
            tip = branch_tip(repo)
            rec = {"event": {k: ev[k] for k in
                             ("channel", "source", "repo", "intro_sha",
                              "intro_date", "added_lines", "files")}}
            for label, sha in points.items():
                checkout(repo, sha)
                m = {"sha": sha}
                if a.designite:
                    m["designite"] = designite(repo, ev)
                if sonar:
                    key = f"{ev['repo'].replace('/', '_')}__{sha[:8]}"
                    try:
                        sonar.scan(repo, key)
                        m["sonar"] = attribute(sonar.issues(key), ev)
                    except Exception as e:
                        m["sonar"] = {"error": str(e)[:300]}
                rec[label] = m
                print(f"    {label}: " + ", ".join(
                    f"{k}={json.dumps(v)[:90]}" for k, v in m.items() if k != "sha"))
            out.write(json.dumps(rec) + "\n")
            out.flush()
            # Leave the clone on its branch tip. Measuring detaches HEAD, and a
            # stale HEAD silently changes the history counts that p3 and p5 read.
            checkout(repo, tip)
    print(f"\n-> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
