"""T-011: a polite HTTP client shared by every fetcher in pipeline/sources/.

Politeness has three layers:
  * an identified User-Agent and a 30-second timeout on every request;
  * at most one request per second to any given host, enforced in-process
    (each `python -m pipeline.scan` run is a single process, so a module-level
    dict of last-request times per host is enough -- no cross-process lock needed);
  * an ETag / Last-Modified cache under `config.CACHE_DIR`, so an unchanged
    endpoint costs the remote server a cheap 304 instead of a full response.

Separately, `due_for_poll()` enforces a per-*source* (not per-host) minimum
interval of 55 minutes by reading `source_health.last_run`, so the same employer
is not re-polled inside the same hour even across several `scan` invocations.
`pipeline.scan` calls it once per registry entry before fetching; `--force`
bypasses it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from pipeline import config


def _utcnow_naive() -> datetime:
    """Naive UTC now, matching SQLite's `datetime('now')` format. `datetime.utcnow()`
    is deprecated (Python 3.12+) in favour of `datetime.now(timezone.utc)`, which is
    timezone-aware and cannot be compared/subtracted against the naive datetimes
    `datetime.strptime` parses source_health.last_run into -- so the aware value is
    immediately stripped back to naive UTC here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

USER_AGENT = "CareerOS personal job scanner (candidate@example.com)"
TIMEOUT_SECONDS = 30
MIN_HOST_INTERVAL_SECONDS = 1.0
MIN_SOURCE_INTERVAL_MINUTES = 55

_BASE_HEADERS = {"User-Agent": USER_AGENT}
_session = requests.Session()
_session.headers.update(_BASE_HEADERS)

# Per-host politeness throttle, process-lifetime only.
_last_request_at: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Per-host rate limiting
# ---------------------------------------------------------------------------

def _throttle(url: str) -> None:
    """Sleep just long enough that this host sees at most 1 request/second."""
    host = urlparse(url).netloc
    now = time.monotonic()
    last = _last_request_at.get(host)
    if last is not None:
        wait = MIN_HOST_INTERVAL_SECONDS - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


# ---------------------------------------------------------------------------
# ETag / Last-Modified disk cache (GET only -- POST bodies vary per call and
# are not meaningfully cacheable here)
# ---------------------------------------------------------------------------

def _cache_path(url: str, params: dict | None) -> Path:
    raw = "GET|" + url + "|" + json.dumps(params or {}, sort_keys=True, default=str)
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{key}.json"


def _load_cache(url: str, params: dict | None) -> dict[str, Any] | None:
    path = _cache_path(url, params)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(url: str, params: dict | None, resp: requests.Response) -> None:
    entry = {
        "url": url,
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
        "encoding": resp.encoding or "utf-8",
        "body": resp.text,
        "fetched_at": _utcnow_naive().isoformat(),
    }
    if not entry["etag"] and not entry["last_modified"]:
        return  # nothing to condition a future request on
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url, params).write_text(json.dumps(entry), encoding="utf-8")


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    use_cache: bool = True,
) -> requests.Response:
    """GET `url` with the polite headers, per-host throttling and (if `use_cache`)
    ETag/Last-Modified conditional caching.

    Never raises for 4xx/5xx: fetchers that need to tell a real error (Greenhouse's
    404 for a wrong token, Workday's 422 for a wrong site) apart from a network
    problem check `response.status_code` themselves, or call
    `response.raise_for_status()` once they are done with that check.

    On a 304, the cached body is spliced back into the returned Response (status
    forced to 200) so callers can use `.json()`/`.text` exactly as for a fresh 200.
    """
    _throttle(url)
    req_headers = {**_BASE_HEADERS, **(headers or {})}
    cache_entry = _load_cache(url, params) if use_cache else None
    if cache_entry:
        if cache_entry.get("etag"):
            req_headers["If-None-Match"] = cache_entry["etag"]
        if cache_entry.get("last_modified"):
            req_headers["If-Modified-Since"] = cache_entry["last_modified"]

    resp = _session.get(url, params=params, headers=req_headers, auth=auth, timeout=TIMEOUT_SECONDS)

    if use_cache and resp.status_code == 304 and cache_entry:
        resp._content = cache_entry["body"].encode(cache_entry.get("encoding") or "utf-8")
        resp.status_code = 200
        resp.encoding = cache_entry.get("encoding") or "utf-8"
    elif use_cache and resp.status_code == 200:
        _save_cache(url, params, resp)

    return resp


def post(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """POST `url` with the polite headers and per-host throttling. Not cached
    (Workday's cxs endpoint is the only caller, and its body varies per page)."""
    _throttle(url)
    req_headers = {**_BASE_HEADERS, **(headers or {})}
    return _session.post(url, json=json, headers=req_headers, timeout=TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# Per-source (not per-host) minimum poll interval
# ---------------------------------------------------------------------------

def due_for_poll(source: str, conn: sqlite3.Connection, force: bool = False) -> bool:
    """True if `source` (a source_health.source key, e.g. a registry company name)
    has never been polled, was last polled at least MIN_SOURCE_INTERVAL_MINUTES ago,
    or `force` is set.
    """
    if force:
        return True
    row = conn.execute("SELECT last_run FROM source_health WHERE source = ?", (source,)).fetchone()
    if row is None or row["last_run"] is None:
        return True
    last_run = datetime.strptime(row["last_run"], "%Y-%m-%d %H:%M:%S")
    return _utcnow_naive() - last_run >= timedelta(minutes=MIN_SOURCE_INTERVAL_MINUTES)
