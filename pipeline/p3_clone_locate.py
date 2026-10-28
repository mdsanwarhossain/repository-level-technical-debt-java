#!/usr/bin/env python3
"""
Stage 3 - clone each candidate repository and pin down the introduction event.

An *introduction event* is the (commit, file, line-range) triple where
source-attributed code entered the repository. Everything downstream - severity,
remediation effort, survival, change-proneness - is measured relative to it, so
this stage decides the quality of the whole study.

Two cases, because the two channel kinds give us different starting points:

  commit channels (AI trailers, SO URL in commit message)
      The candidate already names the commit. We take its added Java lines.

  code channels (SO answer URL in a .java file)
      The candidate names a file in the current checkout. We walk history
      backwards for the commit that first introduced that URL
      (`git log -S <url> --reverse`), which is the introduction event.

Repositories are cloned with full history - shallow clones cannot support
survival analysis.

Usage:
    python -m pipeline.p3_clone_locate --channel so_code_url --limit 20
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from .config import INTERIM, REPOS, MIN_COMMITS_AFTER_INTRO, MIN_PROJECT_AGE_DAYS

SO_URL = re.compile(r"https?://(?:[a-z]+\.)?stackoverflow\.com/(?:a|questions)/\d+(?:/\d+)?")
JAVA = re.compile(r"\.java$")

# What a human rater has to judge is the attributing line itself - the trailer
# or the cited URL - not the commit subject, which is often in another language
# and says nothing about provenance.
# Why candidates were rejected, reported per run so the channels' real yield is
# visible instead of inferred.
DROPS: "Counter[str]" = Counter()

PROVENANCE_LINE = re.compile(
    r"^.*(co-authored-by:\s*(claude|copilot|cursor|devin|codex)"
    r"|aider:|chatgpt|gpt-4|stackoverflow\.com/(a|questions)/\d+).*$",
    re.I | re.M)


def provenance_evidence(message: str, fallback: str) -> str:
    """The line(s) in the commit message that caused the attribution."""
    hits = [m.group(0).strip() for m in PROVENANCE_LINE.finditer(message or "")]
    seen, uniq = set(), []
    for h in hits:
        if h.lower() not in seen:
            seen.add(h.lower())
            uniq.append(h)
    return " | ".join(uniq)[:300] if uniq else fallback


def run(cmd: list[str], cwd: Path | None = None, check=True) -> str:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}... -> {p.returncode}: {p.stderr[:300]}")
    return p.stdout


def restore_tip(repo: Path) -> str | None:
    """Put the clone back on its branch tip.

    p4_measure checks out individual commits and leaves the repository detached
    at whichever one it measured last. Any later `sha..HEAD` count then measures
    history against that stale HEAD instead of the branch tip, which silently
    changes which events look eligible. So every stage that reads HEAD restores
    the tip first.
    """
    branch = run(["git", "symbolic-ref", "--short", "HEAD"], cwd=repo,
                 check=False).strip()
    if not branch:
        branch = run(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                     cwd=repo, check=False).strip().removeprefix("origin/")
    if not branch:
        return None
    run(["git", "checkout", "--quiet", "--force", branch], cwd=repo, check=False)
    return branch


def is_usable(repo: Path) -> bool:
    """A full clone with a working tree. Blobless discovery clones have the
    commit graph but no file contents, so they cannot be diffed."""
    if not (repo / ".git").exists():
        return False
    if run(["git", "config", "--get", "remote.origin.partialclonefilter"],
           cwd=repo, check=False).strip():
        return False
    return bool(run(["git", "ls-files"], cwd=repo, check=False).strip())


def clone(full_name: str, no_clone: bool = False) -> Path | None:
    dest = REPOS / full_name.replace("/", "__")
    if (dest / ".git").exists():
        if no_clone and not is_usable(dest):
            DROPS["not-promoted"] += 1
            return None
        restore_tip(dest)
        return dest
    if no_clone:
        DROPS["not-cloned"] += 1
        return None
    print(f"  cloning {full_name}")
    try:
        run(["git", "clone", "--quiet", "--single-branch",
             f"https://github.com/{full_name}.git", str(dest)])
    except RuntimeError as e:
        print(f"  ! clone failed: {e}")
        return None
    return dest


def head_is_tip(repo: Path) -> bool:
    """Guard for the stale-HEAD trap: HEAD must equal the remote branch tip."""
    head = run(["git", "rev-parse", "HEAD"], cwd=repo, check=False).strip()
    branch = run(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                 cwd=repo, check=False).strip()
    if not branch:
        return True
    tip = run(["git", "rev-parse", branch], cwd=repo, check=False).strip()
    return not tip or head == tip


def added_java_lines(repo: Path, sha: str) -> dict[str, list[tuple[int, int]]]:
    """Added line ranges per .java file in one commit, from the unified diff."""
    diff = run(["git", "show", "--unified=0", "--format=", sha], cwd=repo, check=False)
    out: dict[str, list[tuple[int, int]]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            p = line[6:]
            path = p if JAVA.search(p) else None
        elif line.startswith("@@") and path:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                if count:
                    out.setdefault(path, []).append((start, start + count - 1))
    return out


def first_commit_touching(repo: Path, path: str, needle: str) -> str | None:
    """Oldest commit whose diff on `path` adds or removes `needle`."""
    out = run(["git", "log", "--reverse", "--format=%H", f"-S{needle}",
               "--", path], cwd=repo, check=False)
    shas = out.split()
    return shas[0] if shas else None


def is_merge(repo: Path, sha: str) -> bool:
    """A merge commit introduces no authored lines of its own.

    `git show --unified=0` prints nothing for a merge, so such candidates were
    already being dropped as "touched no Java" - the right outcome reached for
    the wrong stated reason. Detecting it explicitly keeps the drop reportable:
    the `ai_message_*` channels are dominated by merges of `copilot/<branch>`
    pull requests, and the paper should say so rather than leave a silent filter
    doing the work.
    """
    out = run(["git", "rev-list", "--parents", "-n1", sha], cwd=repo, check=False)
    return len(out.split()) > 2


def commit_meta(repo: Path, sha: str) -> dict:
    out = run(["git", "show", "-s", "--format=%H%x1f%aI%x1f%an%x1f%s", sha], cwd=repo)
    h, date, author, subject = out.strip().split("\x1f")
    return {"sha": h, "date": date, "author": author, "subject": subject}


def history_after(repo: Path, sha: str) -> int:
    """How many commits follow the introduction on this branch."""
    out = run(["git", "rev-list", "--count", f"{sha}..HEAD"], cwd=repo, check=False)
    return int(out.strip() or 0)


def project_age_days(repo: Path) -> int:
    first = run(["git", "log", "--reverse", "--format=%aI", "--max-parents=0"],
                cwd=repo, check=False).split()
    if not first:
        return 0
    t = datetime.fromisoformat(first[0])
    return (datetime.now(timezone.utc) - t).days


def locate(cand: dict, repo: Path) -> dict | None:
    """Turn one candidate into a fully specified introduction event."""
    channel = cand["channel"]

    if "sha" in cand:                        # commit channel
        sha = cand["sha"]
        try:
            meta = commit_meta(repo, sha)
        except RuntimeError:
            DROPS["not-on-branch"] += 1
            return None                      # commit not on the cloned branch
        if is_merge(repo, sha):
            DROPS["merge-commit"] += 1
            return None
        files = added_java_lines(repo, sha)
        if not files:
            DROPS["no-java-added"] += 1
            return None
        evidence = provenance_evidence(
            cand.get("message", ""),
            fallback=f"[no matching line found] {cand.get('message', '')[:200]}")
    else:                                    # code channel
        path = cand["path"]
        src = (repo / path)
        if not src.exists():
            DROPS["file-missing"] += 1
            return None
        try:
            text = src.read_text(errors="replace")
        except OSError:
            return None
        urls = SO_URL.findall(text)
        if not urls:
            return None
        sha = first_commit_touching(repo, path, urls[0])
        if not sha:
            return None
        meta = commit_meta(repo, sha)
        files = added_java_lines(repo, sha)
        files = {path: files.get(path, [])} if path in files else {}
        if not files.get(path):
            return None
        evidence = urls[0]

    n_after = history_after(repo, sha)
    age = project_age_days(repo)
    event = {
        "channel": channel,
        "source": "ai" if channel.startswith("ai_") else "so",
        "repo": cand["repo"],
        "stars": cand.get("stars"),
        "intro_sha": sha,
        "intro_date": meta["date"],
        "intro_subject": meta["subject"],
        "evidence": evidence,
        "files": {p: r for p, r in files.items()},
        "added_lines": sum(b - a + 1 for r in files.values() for a, b in r),
        "commits_after_intro": n_after,
        "project_age_days": age,
        "eligible": n_after >= MIN_COMMITS_AFTER_INTRO and age >= MIN_PROJECT_AGE_DAYS,
    }
    return event


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", action="append", required=True)
    ap.add_argument("--limit", type=int, default=25, help="candidates per channel")
    ap.add_argument("--no-clone", action="store_true",
                    help="only use repositories already promoted to a full "
                         "clone; do not fetch anything")
    ap.add_argument("--per-repo", type=int, default=0,
                    help="cap events per repository, so one huge project does "
                         "not dominate the corpus")
    a = ap.parse_args()

    events, stats = [], {}
    for ch in a.channel:
        f = INTERIM / f"candidates_{ch}.jsonl"
        if not f.exists():
            print(f"! no candidates for {ch} - run p1_provenance_probe first")
            continue
        cands = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        cands = [c for c in cands if c.get("java_eligible") is not False]
        print(f"\n[{ch}] {len(cands)} java-eligible candidates, taking {a.limit}")
        kept = 0
        per_repo: dict[str, int] = {}
        for cand in cands[: a.limit]:
            if a.per_repo:
                n = per_repo.get(cand["repo"], 0)
                if n >= a.per_repo:
                    continue
            repo = clone(cand["repo"], no_clone=a.no_clone)
            if not repo:
                continue
            ev = locate(cand, repo)
            if ev:
                events.append(ev)
                kept += 1
                per_repo[cand["repo"]] = per_repo.get(cand["repo"], 0) + 1
                mark = "" if ev["eligible"] else "  (ineligible)"
                print(f"  + {ev['repo']} {ev['intro_sha'][:8]} "
                      f"{ev['added_lines']} lines, {ev['commits_after_intro']} commits after{mark}")
        stats[ch] = {"candidates": min(len(cands), a.limit), "located": kept}

    out = INTERIM / "introduction_events.jsonl"
    with out.open("a") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    print(f"\nlocated {len(events)} introduction events "
          f"({sum(1 for e in events if e['eligible'])} eligible) -> {out}")
    for ch, s in stats.items():
        print(f"  {ch:22s} {s['located']}/{s['candidates']}")
    if DROPS:
        print("\nrejected candidates by reason:")
        for reason, n in DROPS.most_common():
            print(f"  {reason:18s} {n}")


if __name__ == "__main__":
    sys.exit(main())
