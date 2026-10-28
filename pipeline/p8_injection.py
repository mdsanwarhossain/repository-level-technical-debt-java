#!/usr/bin/env python3
"""
Stage 8 - the controlled injection sub-study.

The mining study (stages 1-7) answers what debt actually lands in repositories
and what it costs over time, but it cannot isolate the snippet from the project
that adopted it: repositories that adopt AI tooling differ systematically from
those that do not. This sub-study supplies the missing counterfactual.

Design - paired, within-host:

    For each question in the snippet corpus we have two answers to the same
    problem, one from Stack Overflow and one from ChatGPT. Both are integrated
    into the *same* host project at the *same* site. The host is therefore its
    own control, and any difference in repository-wide smells or SQALE
    remediation effort is attributable to the snippet rather than to the
    project, the domain or the team.

    baseline  host at HEAD, unmodified
    + so      host + the Stack Overflow variant
    + gpt     host + the ChatGPT variant

    outcome = measure(host + variant) - measure(baseline)

What this buys that the snippet study could not: architecture smells are
*repository-scoped* by construction. DesigniteJava can only report cyclic
dependencies, god components or scattered functionality when it can see a whole
project, which is precisely the editor's objection to snippet-level measurement.

Honest limitation, which belongs in the paper and not in a footnote: injection
as a new compilation unit does not exercise call-site coupling, so it measures
architectural *conformance* of the added code, not the coupling a human
integrator would have created by wiring it into existing classes. It is a lower
bound on integration debt.

Usage:
    python -m pipeline.p8_injection --pick-hosts
    python -m pipeline.p8_injection --run --hosts 5 --snippets 50
"""
from __future__ import annotations
import argparse, csv, json, re, shutil, subprocess, sys
from pathlib import Path
from .config import INTERIM, FINAL, REPOS, ROOT
from . import designite as dj

# Host projects: real, maintained, architecturally distinct Java systems.
# Variety matters - a result that holds only in one architectural style is not
# a result. Sizes span two orders of magnitude.
DEFAULT_HOSTS = [
    "apache/commons-lang",      # utility library, flat, few dependencies
    "google/gson",              # focused library, clean layering
    "apache/commons-io",        # utility library, larger surface
    "junit-team/junit4",        # framework, extension-point heavy
    "square/retrofit",          # client library, annotation-driven
    "apache/commons-collections",       # data-structure library
    "spring-projects/spring-petclinic",  # layered application, DI-heavy
    "apache/maven",                      # large multi-module tool
]
# Deliberately NOT a host: iluwatar/java-design-patterns. It is ~150 independent
# single-pattern demo modules with no dominant one (the best source root covers
# 1% of its files), so it has no single architecture for an injected snippet to
# conform to or violate. `root_coverage` in the host record is what makes this
# visible - treat a low value as a signal to drop the host, not to proceed.

SNIPPET_CSV = Path("/home/mdsanwarhossain/Downloads/paper software evolution journal"
                   "/KAje lagabo/dataset_clean.csv")
def run(cmd, cwd=None, check=True, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} -> {p.returncode}: {p.stderr[-300:]}")
    return p.stdout


def pick_hosts(names: list[str]):
    out = []
    for full in names:
        dest = REPOS / ("host__" + full.replace("/", "__"))
        if not (dest / ".git").exists():
            print(f"  cloning {full}")
            try:
                run(["git", "clone", "--quiet", "--depth", "1",
                     f"https://github.com/{full}.git", str(dest)])
            except RuntimeError as e:
                print(f"  ! {full}: {e}")
                continue
        java = list(dest.rglob("*.java"))
        pkg_root = _main_source_root(dest)
        in_root = sum(1 for _ in pkg_root.rglob("*.java")) if pkg_root else 0
        rec = {"host": full, "path": str(dest), "java_files": len(java),
               "source_root": str(pkg_root) if pkg_root else None,
               "java_files_in_root": in_root,
               "root_coverage": round(in_root / len(java), 3) if java else 0.0,
               "loc": sum(_count_lines(f) for f in java[:5000])}
        out.append(rec)
        print(f"  {full}: {rec['java_files']} java files, ~{rec['loc']} LOC, "
              f"root={pkg_root.relative_to(dest) if pkg_root else 'NOT FOUND'} "
              f"({in_root} files, {100 * rec['root_coverage']:.0f}% of the project)")
    (INTERIM / "injection_hosts.json").write_text(json.dumps(out, indent=2))
    print(f"\n-> {INTERIM / 'injection_hosts.json'}")


def _count_lines(f: Path) -> int:
    try:
        return sum(1 for _ in f.open(errors="replace"))
    except OSError:
        return 0


# Multi-release source trees (src/main/java9, java16, ...) hold a handful of
# version-specific overrides. Injecting there would compile only under that
# release and is not representative of the host.
MULTI_RELEASE = re.compile(r"/java\d+$")


def _main_source_root(repo: Path) -> Path | None:
    """Where a new compilation unit belongs.

    Picking the first directory that matches `src/main/java` is wrong for the
    two shapes that matter here: multi-module Maven projects, where it lands in
    whichever module sorts first, and multi-release jars, where it lands in a
    `java16/` override tree. Choose instead the main-source root holding the
    most Java files - the project's dominant module.
    """
    roots: list[Path] = []
    for pattern in ("src/main/java", "*/src/main/java", "*/*/src/main/java"):
        roots.extend(d for d in repo.glob(pattern) if d.is_dir())
    roots = [d for d in roots if not MULTI_RELEASE.search(d.as_posix())]
    if not roots:
        for cand in ("src/java", "java", "src"):
            d = repo / cand
            if d.is_dir() and any(d.rglob("*.java")):
                roots.append(d)
    if roots:
        return max(roots, key=lambda d: sum(1 for _ in d.rglob("*.java")))
    java = sorted(repo.rglob("*.java"), key=lambda f: len(f.parts))
    return java[0].parent if java else None


IDENT = re.compile(r"[^A-Za-z0-9_]")


def wrap_snippet(code: str, class_name: str, package: str | None) -> str:
    """Make a snippet into a compilable-looking compilation unit.

    The snippet is placed in a method body unless it already declares a class,
    in which case it is used as-is with the class renamed. Statements that only
    make sense inside a method (the common Stack Overflow answer shape) are
    wrapped; declarations are not.
    """
    header = f"package {package};\n\n" if package else ""
    has_class = re.search(r"\b(class|interface|enum|record)\s+\w+", code)
    if has_class:
        body = re.sub(r"\b(class|interface|enum|record)\s+(\w+)",
                      lambda m: f"{m.group(1)} {class_name}", code, count=1)
        return header + body + "\n"
    # Hoist imports out of the method body - they are illegal there.
    imports = re.findall(r"^\s*import\s+[\w.*]+;\s*$", code, flags=re.M)
    rest = re.sub(r"^\s*import\s+[\w.*]+;\s*$", "", code, flags=re.M)
    indent = "\n".join("        " + l for l in rest.splitlines())
    return (header + "\n".join(i.strip() for i in imports) + "\n\n"
            + f"public class {class_name} {{\n"
            + "    public void apply() throws Exception {\n"
            + indent + "\n    }\n}\n")


def package_of(source_root: Path, target_dir: Path) -> str | None:
    rel = target_dir.relative_to(source_root)
    return ".".join(rel.parts) if rel.parts else None


def designite_counts(repo: Path) -> dict:
    """Whole-repository smell counts, or {"_error": ...} on failure.

    Failure is reported explicitly rather than as an empty dict: an empty dict
    is indistinguishable from "no smells", which is how every delta in the first
    calibration run came out as zero.
    """
    try:
        return dj.counts(dj.run_designite(repo))
    except (dj.DesigniteError, subprocess.TimeoutExpired, OSError) as e:
        return {"_error": str(e)[:300]}


def delta(after: dict, before: dict) -> dict:
    return dj.delta(after, before)


def run_study(n_hosts: int, n_snippets: int):
    hosts_file = INTERIM / "injection_hosts.json"
    if not hosts_file.exists():
        sys.exit("run --pick-hosts first")
    hosts = [h for h in json.loads(hosts_file.read_text()) if h["source_root"]][:n_hosts]
    if not SNIPPET_CSV.exists():
        sys.exit(f"snippet corpus not found at {SNIPPET_CSV}")
    with SNIPPET_CSV.open(newline="") as fh:
        snippets = [r for r in csv.DictReader(fh) if r["snippet_integrity"] == "ok"]
    snippets = snippets[:n_snippets]
    print(f"{len(hosts)} hosts x {len(snippets)} snippet pairs "
          f"x 2 variants = {len(hosts) * len(snippets) * 2} measurements")

    out_path = INTERIM / "injection_results.jsonl"
    with out_path.open("a") as out:
        for h in hosts:
            repo, root = Path(h["path"]), Path(h["source_root"])
            print(f"\n[{h['host']}] baseline ...")
            baseline = designite_counts(repo)
            if "_error" in baseline:
                print(f"  ! baseline failed: {baseline['_error']}")
                continue
            print("  baseline: " + ", ".join(
                f"{k}={v}" for k, v in sorted(baseline.items())
                if k.startswith("total:") or k.startswith("methods")))

            target_dir = root / "injected"
            for s in snippets:
                qid = s["so_question_id"]
                for variant, col in (("so", "code_snippet_so"),
                                     ("gpt", "code_snippet_gpt")):
                    code = s[col]
                    if not code or not code.strip():
                        continue
                    cls = f"Injected{variant.upper()}{qid}"
                    target_dir.mkdir(parents=True, exist_ok=True)
                    f = target_dir / f"{cls}.java"
                    f.write_text(wrap_snippet(code, cls,
                                              package_of(root, target_dir)))
                    after = designite_counts(repo)
                    shutil.rmtree(target_dir, ignore_errors=True)
                    if "_error" in after:
                        continue
                    d = delta(after, baseline)
                    rec = {"host": h["host"], "host_java_files": h["java_files"],
                           "question_id": qid, "variant": variant,
                           "snippet_loc": len(code.splitlines()),
                           "delta": d,
                           "new_architecture_smells":
                               d.get("total:architecture_level", 0),
                           "new_design_smells": d.get("total:design", 0),
                           "new_implementation_smells":
                               d.get("total:implementation", 0),
                           "new_cc_gt3_methods": d.get("methods_cc_gt3", 0),
                           "new_methods": d.get("methods", 0)}
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    print(f"  q{qid} {variant}: arch{rec['new_architecture_smells']:+d} "
                          f"design{rec['new_design_smells']:+d} "
                          f"impl{rec['new_implementation_smells']:+d} "
                          f"cc>3{rec['new_cc_gt3_methods']:+d}")
    print(f"\n-> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pick-hosts", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--hosts", type=int, default=5)
    ap.add_argument("--snippets", type=int, default=50)
    a = ap.parse_args()
    if a.pick_hosts:
        pick_hosts(DEFAULT_HOSTS)
    elif a.run:
        run_study(a.hosts, a.snippets)
    else:
        ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
