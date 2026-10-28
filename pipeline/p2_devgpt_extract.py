#!/usr/bin/env python3
"""
Stage 2 - extract Java introduction-event candidates from DevGPT.

DevGPT (MSR 2024 Mining Challenge, CC-BY-4.0, latest snapshot 2025-07-24) links
developer-shared ChatGPT conversations to the commits, pull requests, issues and
discussions that followed. For AI provenance it is the strongest publicly
available signal, because the link was created by the developer sharing the
conversation rather than inferred by us.

Its weakness is the mirror image: it only covers code whose author chose to
share a conversation, and only from 2023. That is why it is one channel among
several rather than the whole AI side of the study - see the trailer channels in
p1_provenance_probe.

The schema is discovered at runtime rather than hard-coded, because the 2024
SQLite release and the 2025 zip release do not agree on table names.

Usage:
    python -m pipeline.p2_devgpt_extract --schema     # inspect only
    python -m pipeline.p2_devgpt_extract
"""
from __future__ import annotations
import argparse, json, re, sqlite3, sys
from .config import DEVGPT_SQLITE, INTERIM
from . import store

SHA = re.compile(r"\b[0-9a-f]{40}\b")
GH_COMMIT = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/commit/([0-9a-f]{7,40})")
GH_REPO = re.compile(r"github\.com/([\w.-]+/[\w.-]+)")


def tables(con) -> dict[str, list[str]]:
    out = {}
    for (name,) in con.execute("select name from sqlite_master where type='table'"):
        cols = [r[1] for r in con.execute(f'pragma table_info("{name}")')]
        out[name] = cols
    return out


def show_schema(con):
    for name, cols in sorted(tables(con).items()):
        n = con.execute(f'select count(*) from "{name}"').fetchone()[0]
        print(f"\n{name}  ({n} rows)")
        print("  " + ", ".join(cols))
        for row in con.execute(f'select * from "{name}" limit 1'):
            for c, v in zip(cols, row):
                s = str(v)
                print(f"    {c:24s} {s[:90]}{'...' if len(s) > 90 else ''}")


def pick(cols: list[str], *wanted: str) -> str | None:
    low = {c.lower(): c for c in cols}
    for w in wanted:
        if w in low:
            return low[w]
    for w in wanted:
        for lc, orig in low.items():
            if w in lc:
                return orig
    return None


def extract(con) -> list[dict]:
    """Pull every (repo, sha) pair DevGPT associates with a shared conversation.

    Schema notes for the 2024 SQLite release, learned the hard way:

      * the `commit` table exists but is EMPTY. The commit links survive only in
        `sharing.mentions`, as a JSON-ish blob holding the MentionedURL.
      * `pull_request` carries `repo_name`, `repo_language` and `commit_sha`,
        but every Java row has `commit_sha` NULL, so it yields nothing usable.
      * `sharing.type` is one of issue / commit / hn / pr / discussion. Only
        `commit` gives a commit-level introduction event.

    So the extraction is: parse `sharing.mentions` for commit URLs. Repository
    language is not recorded there, so Java filtering is deferred to
    p3_clone_locate.
    """
    schema = tables(con)
    con.row_factory = sqlite3.Row
    found: dict[tuple[str, str], dict] = {}

    if "sharing" in schema:
        for r in con.execute("select id, url, mentions, dateofconversation, type "
                             "from sharing where type = 'commit'"):
            m = GH_COMMIT.search(r["mentions"] or "")
            if not m:
                continue
            repo, sha = m.group(1).removesuffix(".git"), m.group(2)
            found[(repo, sha)] = {
                "channel": "ai_devgpt",
                "repo": repo,
                "sha": sha,
                "devgpt_table": "sharing",
                "conversation_url": r["url"],
                "conversation_date": r["dateofconversation"],
            }

    # If a future DevGPT release populates `commit`, use it too - it carries the
    # repository language, which saves a clone per candidate.
    if "commit" in schema and con.execute('select count(*) from "commit"').fetchone()[0]:
        for r in con.execute('select url, repo_name, repo_language, commit_sha '
                             'from "commit"'):
            blob = " ".join(str(r[k]) for k in r.keys() if r[k])
            m = GH_COMMIT.search(blob)
            repo = m.group(1) if m else (r["repo_name"] or None)
            sha = (m.group(2) if m else None) or (
                SHA.search(str(r["commit_sha"] or "")) or [None])
            if isinstance(sha, re.Match):
                sha = sha.group(0)
            if repo and sha:
                found.setdefault((repo.removesuffix(".git"), sha), {
                    "channel": "ai_devgpt",
                    "repo": repo.removesuffix(".git"),
                    "sha": sha,
                    "devgpt_table": "commit",
                    "repo_language": r["repo_language"],
                })

    return list(found.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true", help="print the schema and exit")
    a = ap.parse_args()

    if not DEVGPT_SQLITE.exists():
        sys.exit(f"{DEVGPT_SQLITE} not found - download it from "
                 "https://zenodo.org/records/10603053")
    con = sqlite3.connect(DEVGPT_SQLITE)

    if a.schema:
        show_schema(con)
        return 0

    cands = extract(con)
    by_table: dict[str, int] = {}
    for c in cands:
        by_table[c["devgpt_table"]] = by_table.get(c["devgpt_table"], 0) + 1
    new, total = store.merge("ai_devgpt", cands)
    out = store.path_for("ai_devgpt")
    print(f"merged +{new} new, {total} in channel")
    print(f"{len(cands)} distinct (repo, commit) pairs from "
          f"{len({c['repo'] for c in cands})} repositories")
    for t, n in sorted(by_table.items(), key=lambda kv: -kv[1]):
        print(f"  {t:30s} {n}")
    print(f"-> {out}")
    print("\nJava filtering and eligibility happen in p3_clone_locate: the "
          "sharing table does not record repository language.")
    print("DevGPT is thin for Java - in this release Java is roughly 6% of the "
          "linked repositories - so it supplements the trailer channels rather "
          "than carrying the AI arm on its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
