"""Shared Langfuse API client for trace analysis scripts.

Handles credential loading, trace fetching with local caching, and common
timestamp parsing.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

CACHE_DIR = Path(__file__).parent / ".trace_cache"


def load_creds() -> tuple[str, str, str]:
    """Load Langfuse credentials from .env.local or .env."""
    for p in [".env.local", ".env"]:
        if Path(p).exists():
            load_dotenv(p, override=True)
    host = (
        os.environ.get("LANGFUSE_HOST")
        or os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")
    ).rstrip("/")
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    return host, pk, sk


def fetch_trace(trace_id: str, *, use_cache: bool = True) -> dict:
    """Fetch a trace from Langfuse, with optional local file cache.

    Cached traces are stored in scripts/.trace_cache/<trace_id>.json.
    Pass use_cache=False to force a fresh fetch.
    """
    cache_path = CACHE_DIR / f"{trace_id}.json"

    if use_cache and cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)

    host, pk, sk = load_creds()
    url = f"{host}/api/public/traces/{trace_id}"
    r = requests.get(url, auth=(pk, sk), timeout=60)
    r.raise_for_status()
    data = r.json()

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f)

    return data


def parse_ts(ts: str | None) -> datetime | None:
    """Parse a Langfuse ISO timestamp string into a datetime."""
    if not ts:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"]:
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            pass
    return None


def find_ancestor_agent(obs_id: str, obs_by_id: dict[str, dict], depth: int = 0) -> dict | None:
    """Walk the parent chain to find the nearest agent:* span (excluding agent:ceo)."""
    if depth > 20:
        return None
    obs = obs_by_id.get(obs_id)
    if not obs:
        return None
    if (
        obs["type"] == "SPAN"
        and obs["name"].startswith("agent:")
        and obs["name"] != "agent:ceo"
    ):
        return obs
    pid = obs.get("parentObservationId")
    if pid:
        return find_ancestor_agent(pid, obs_by_id, depth + 1)
    return None


def get_agent_spans(observations: list[dict]) -> list[dict]:
    """Return agent spans sorted by start time (excluding agent:ceo)."""
    return sorted(
        [
            o for o in observations
            if o["type"] == "SPAN"
            and o["name"].startswith("agent:")
            and o["name"] != "agent:ceo"
        ],
        key=lambda o: o.get("startTime", ""),
    )


def truncate(text: str | None, limit: int = 500) -> str:
    """Truncate text to limit chars, collapsing newlines."""
    if not text:
        return "(empty)"
    s = str(text).replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit] + "..."
    return s
