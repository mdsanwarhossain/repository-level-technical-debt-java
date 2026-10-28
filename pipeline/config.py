"""Shared configuration for the repository-level technical-debt study."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
FINAL = ROOT / "data" / "final"
REPOS = ROOT / "repos"
LOGS = ROOT / "logs"
for d in (RAW, INTERIM, FINAL, REPOS, LOGS):
    d.mkdir(parents=True, exist_ok=True)

DEVGPT_SQLITE = RAW / "devgpt.sqlite"

# Only Java, to stay comparable with the snippet study.
LANGUAGE = "java"

# A repository must look maintained, or survival analysis is meaningless.
MIN_STARS = 10
MIN_COMMITS_AFTER_INTRO = 5      # editor's "next 5 to 10 commits"
MIN_PROJECT_AGE_DAYS = 180

# Matched observation window. The asymmetric-exposure threat: LLM-attributed
# code only exists from 2023, SO-attributed code goes back a decade. Every
# survival comparison is censored at this horizon, measured from the
# introduction commit, not from calendar time.
OBSERVATION_WINDOW_DAYS = 365


def github_token() -> str:
    """Token from GITHUB_TOKEN, or a .github_token file in the project root."""
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    f = ROOT / ".github_token"
    if f.exists():
        return f.read_text().strip()
    raise SystemExit(
        "No GitHub token. Either export GITHUB_TOKEN=... or write the token to "
        f"{f} (that path is git-ignored)."
    )
