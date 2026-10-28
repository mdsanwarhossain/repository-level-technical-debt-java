"""Shared candidate store.

Several discovery stages feed the same `candidates_<channel>.jsonl` files:
p1_provenance_probe (GitHub search), p1b_local_scan (clone scan) and
p2_devgpt_extract. They must therefore *merge*, never truncate - a stage that
opens these files with "w" silently destroys whatever another stage found,
which is a data-loss bug rather than a style preference.

Identity is (repo, sha) for commit candidates and (repo, path) for file
candidates, so re-running a stage is idempotent.
"""
from __future__ import annotations
import json
from pathlib import Path
from .config import INTERIM


def key(row: dict) -> tuple[str, str]:
    return (row.get("repo", ""), row.get("sha") or row.get("path") or "")


def path_for(channel: str) -> Path:
    return INTERIM / f"candidates_{channel}.jsonl"


def load(channel: str) -> list[dict]:
    f = path_for(channel)
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


def merge(channel: str, rows: list[dict]) -> tuple[int, int]:
    """Add `rows` to a channel, keeping existing entries. Returns (new, total).

    An existing row is updated with any new non-null fields rather than
    replaced, so metadata added by p2b_enrich survives a re-discovery.
    """
    existing = {key(r): r for r in load(channel)}
    new = 0
    for r in rows:
        k = key(r)
        if k in existing:
            for field, value in r.items():
                if value is not None and existing[k].get(field) is None:
                    existing[k][field] = value
        else:
            existing[k] = r
            new += 1
    f = path_for(channel)
    with f.open("w") as fh:
        for r in existing.values():
            fh.write(json.dumps(r) + "\n")
    return new, len(existing)
