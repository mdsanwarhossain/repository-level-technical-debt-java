"""Rate-limit-aware GitHub REST client.

The search endpoints are the binding constraint: 30 requests/minute authenticated
and a hard 1000-result ceiling per query. Both are handled here so the mining
scripts can stay readable.
"""
from __future__ import annotations
import time
import requests
from .config import github_token

API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str | None = None, pause: float = 2.2):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token or github_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-td-study",
        })
        self.pause = pause   # search allows 30/min; 2.2s keeps us under it

    def get(self, path: str, **params):
        url = path if path.startswith("http") else f"{API}{path}"
        for attempt in range(6):
            r = self.s.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json(), r.headers
            if r.status_code in (403, 429):
                # Secondary rate limit or abuse detection: obey Retry-After,
                # else back off until the window resets.
                wait = int(r.headers.get("Retry-After", 0)) or 60
                reset = r.headers.get("X-RateLimit-Reset")
                if reset and r.headers.get("X-RateLimit-Remaining") == "0":
                    wait = max(wait, int(reset) - int(time.time()) + 5)
                print(f"    rate-limited, sleeping {wait}s")
                time.sleep(min(wait, 900))
                continue
            if r.status_code == 422:
                # Past the 1000-result ceiling, or a malformed query.
                return {"items": [], "total_count": 0, "_422": r.text[:300]}, r.headers
            r.raise_for_status()
        raise RuntimeError(f"giving up on {url}")

    def search(self, kind: str, query: str, cap: int = 1000, per_page: int = 100):
        """Page a search endpoint. kind is 'code', 'commits' or 'repositories'."""
        seen, page = [], 1
        while len(seen) < cap:
            data, _ = self.get(f"/search/{kind}", q=query, per_page=per_page, page=page)
            items = data.get("items", [])
            if page == 1:
                self.last_total = data.get("total_count", 0)
            if not items:
                break
            seen.extend(items)
            if len(items) < per_page or page * per_page >= 1000:
                break
            page += 1
            time.sleep(self.pause)
        time.sleep(self.pause)
        return seen[:cap]

    def rate_limit(self):
        data, _ = self.get("/rate_limit")
        return data["resources"]
