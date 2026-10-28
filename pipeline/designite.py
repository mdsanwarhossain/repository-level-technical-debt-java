"""DesigniteJava integration.

Written against the actual output of the bundled `tools/DesigniteJava.jar`,
because the names in its documentation do not match what it writes:

    designCodeSmells.csv          Project Name, Package Name, Type Name, Code Smell
    implementationCodeSmells.csv  ... plus Method Name
    typeMetrics.csv               ... NOF NOPF NOM NOPM LOC WMC NC DIT LCOM FANIN FANOUT
    methodMetrics.csv             ... MethodName, LOC, CC, PC

Three things follow, and all three were silently wrong before:

  * The files are camelCase (`designCodeSmells.csv`), not `DesignSmells.csv`.
    Looking for the wrong names returned an empty result that read as "no
    smells" rather than as a failure, so every injection delta came out zero.
  * There is **no `File path` column**. Attribution to a specific file has to go
    through `Package Name` + `Type Name`, which maps to `<package>/<Type>.java`.
  * There is **no separate architecture-smells file**. The community edition
    folds the repository-scoped smells into `designCodeSmells.csv`, so they are
    identified by name instead - see `ARCHITECTURE_LEVEL`.

A run that produces none of the expected files raises. An analyser that quietly
reports nothing is worse than one that fails.

**Granularity caveat, which must reach the paper.** DesigniteJava reports at
class and method granularity with no line numbers, so `counts_for_files` is
*file*-scoped: it counts every smell in a file the introduction touched, not
only the smells on the introduced lines. SonarQube does report line numbers, so
`p4_measure`'s Sonar attribution is *line*-scoped. The two are therefore not
interchangeable and must never be pooled:

    Sonar    "this issue is on a line the commit added"
    Designite "this smell is in a file the commit touched"

Use Sonar for the per-event severity and remediation-cost claims (RQ2), and
Designite for repository-wide architectural context and for continuity with the
snippet study's indicators.
"""
from __future__ import annotations
import csv
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

JAR = Path(__file__).resolve().parent.parent / "tools" / "DesigniteJava.jar"

SMELL_CSV = {
    "design": "designCodeSmells.csv",
    "implementation": "implementationCodeSmells.csv",
}
METRIC_CSV = {"type": "typeMetrics.csv", "method": "methodMetrics.csv"}

# Smells that cannot be judged on a snippet in isolation - they are properties
# of how modules relate to each other, so they only exist once there is a
# repository around the code. These are what makes repository-level measurement
# different in kind from the snippet study, and they carry RQ5.
ARCHITECTURE_LEVEL = {
    "Cyclic-Dependent Modularization",
    "Hub-like Modularization",
    "Broken Modularization",
    "Insufficient Modularization",
    "Unutilized Abstraction",
    "Unnecessary Abstraction",
    "Broken Hierarchy",
    "Missing Hierarchy",
    "Cyclic Hierarchy",
    "Deep Hierarchy",
    "Multipath Hierarchy",
    "Rebellious Hierarchy",
    "Wide Hierarchy",
}

# Indicators the snippet study measured, kept so the two papers can be compared.
SNIPPET_STUDY_SMELLS = {
    "Magic Number", "Empty catch clause", "Complex Method", "Complex Conditional",
    "Deficient Encapsulation", "Insufficient Modularization", "Long Statement",
    "Long Method", "Long Parameter List",
}


class DesigniteError(RuntimeError):
    pass


def run_designite(src: Path, timeout: int = 2400) -> dict:
    """Analyse a source tree. Returns parsed rows plus per-method CC."""
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(
            ["java", "-jar", str(JAR), "-i", str(src), "-o", tmp],
            capture_output=True, text=True, timeout=timeout)
        out = Path(tmp)
        present = {k: out / v for k, v in SMELL_CSV.items() if (out / v).exists()}
        if not present:
            raise DesigniteError(
                f"DesigniteJava wrote none of {list(SMELL_CSV.values())} for "
                f"{src} (exit {p.returncode}): {(p.stderr or p.stdout)[-300:]}")

        smells: dict[str, list[dict]] = {}
        for kind, path in present.items():
            with path.open(newline="", errors="replace") as fh:
                smells[kind] = list(csv.DictReader(fh))

        methods = []
        mpath = out / METRIC_CSV["method"]
        if mpath.exists():
            with mpath.open(newline="", errors="replace") as fh:
                methods = list(csv.DictReader(fh))
        return {"smells": smells, "methods": methods}


def smell_name(row: dict) -> str:
    return (row.get("Code Smell") or row.get("Design Smell")
            or row.get("Implementation Smell") or "unknown").strip()


def type_path(row: dict) -> str:
    """`com.x.Foo` + `Foo` -> `com/x/Foo.java`, to match against git paths."""
    pkg = (row.get("Package Name") or "").strip()
    typ = (row.get("Type Name") or "").strip().split("<")[0]
    if not typ:
        return ""
    return (pkg.replace(".", "/") + "/" if pkg else "") + f"{typ}.java"


def counts(result: dict) -> dict[str, int]:
    """Smell counts repository-wide, keyed `kind:name`, plus roll-ups."""
    out: Counter[str] = Counter()
    for kind, rows in result["smells"].items():
        for r in rows:
            name = smell_name(r)
            out[f"{kind}:{name}"] += 1
            out[f"total:{kind}"] += 1
            if name in ARCHITECTURE_LEVEL:
                out["total:architecture_level"] += 1
            if name in SNIPPET_STUDY_SMELLS:
                out["total:snippet_study_indicators"] += 1
    cc = [int(m["CC"]) for m in result["methods"]
          if (m.get("CC") or "").strip().lstrip("-").isdigit()]
    if cc:
        out["methods"] = len(cc)
        out["methods_cc_gt3"] = sum(1 for c in cc if c > 3)
        out["cc_sum"] = sum(cc)
    return dict(out)


def counts_for_files(result: dict, files: set[str]) -> dict[str, int]:
    """Smell counts restricted to the given repository-relative paths.

    Matching is by suffix because `Package Name` reflects the declared package,
    which is a suffix of the on-disk path under any source root
    (`src/main/java/com/x/Foo.java` vs `com/x/Foo.java`).
    """
    out: Counter[str] = Counter()
    for kind, rows in result["smells"].items():
        for r in rows:
            tp = type_path(r)
            if not tp:
                continue
            if any(f.endswith(tp) for f in files):
                name = smell_name(r)
                out[f"{kind}:{name}"] += 1
                out[f"total:{kind}"] += 1
                if name in ARCHITECTURE_LEVEL:
                    out["total:architecture_level"] += 1
    return dict(out)


def delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    keys = set(after) | set(before)
    return {k: after.get(k, 0) - before.get(k, 0) for k in keys
            if after.get(k, 0) != before.get(k, 0)}
