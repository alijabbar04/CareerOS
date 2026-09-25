"""Pinpoint postings feed (research/D_job_sources.md section 1).

    GET https://{company}.pinpointhq.com/postings.json          (default board)
    GET https://{company}.pinpointhq.com/{board-slug}.json      (a named board)

No auth. Common at mid-tier accountancy firms. Registry entries are
inconsistent about whether `endpoint.url` already includes `/postings.json`,
a named sub-board (Kreston Reeves: `.../student`), or even a scheme, so the
URL is normalised defensively rather than assumed.

Live-confirmed (2026-09-22): a named board's JSON feed is `{slug}.json`, not
`{slug}/postings.json` -- `krestonreeves.pinpointhq.com/student/postings.json`
404s, `krestonreeves.pinpointhq.com/student.json` returns the board's 14
postings in the same `{"data": [...]}` shape as the default board.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting, to_int


def _postings_url(endpoint: dict[str, Any]) -> str:
    raw = (endpoint.get("url") or "").strip()
    if not raw:
        org = endpoint.get("org")
        if not org:
            raise ValueError("pinpoint entry missing endpoint.org/url")
        raw = f"{org}.pinpointhq.com"
    if not raw.startswith("http"):
        raw = f"https://{raw}"
    raw = raw.rstrip("/")
    if raw.endswith("postings.json") or raw.endswith(".json"):
        return raw
    path = urlsplit(raw).path
    if path and path != "/":
        return raw + ".json"  # named board, e.g. .../student -> .../student.json
    return raw + "/postings.json"  # bare domain -> the default board


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    url = _postings_url(endpoint)

    resp = http.get(url)
    resp.raise_for_status()
    data = resp.json()
    # Live response is {"data": [...]}; tolerate a bare list too in case a
    # differently-configured board (or a future Pinpoint API version) returns one.
    postings = data.get("data", []) if isinstance(data, dict) else data
    return parse_postings(postings, entry)


def parse_postings(postings: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("org") or "Pinpoint"
    out = []
    for p in postings or []:
        location = p.get("location")
        location_name = location.get("name") if isinstance(location, dict) else location
        department = p.get("department")
        # Live-confirmed (2026-09-22): unlike Firm C (department always null in the
        # sample fetched), Kreston Reeves' department is a {"id": ..., "name": ...}
        # reference object, not a plain string.
        department_name = department.get("name") if isinstance(department, dict) else department
        out.append(normalize_posting(
            source="pinpoint",
            company_name=company_name,
            external_id=p.get("id"),
            url=p.get("url"),
            title=p.get("title", ""),
            location=location_name,
            employment_type=p.get("employment_type"),
            posted_at=p.get("published_at") or p.get("created_at"),
            closes_at=p.get("deadline_at"),
            salary_min=to_int(p.get("compensation_minimum")),
            salary_max=to_int(p.get("compensation_maximum")),
            currency=p.get("compensation_currency") or "GBP",
            description=html_to_text(p.get("description") or p.get("key_responsibilities")),
            department=department_name,
            raw=p,
        ))
    return out
