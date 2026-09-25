"""Lever Postings API (research/D_job_sources.md section 1).

    GET https://api.lever.co/v0/postings/{company}?mode=json

No auth. `descriptionPlain` is already plain text, so no HTML stripping is
needed for the common case; falls back to stripping `description` (HTML) if
`descriptionPlain` is absent.
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import epoch_ms_to_iso, html_to_text, normalize_posting


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    company = endpoint.get("token") or endpoint.get("company")
    if not company:
        raise ValueError("lever entry missing endpoint.token")
    url = f"https://api.lever.co/v0/postings/{company}"

    resp = http.get(url, params={"mode": "json"})
    if resp.status_code == 404:
        raise ValueError(f"lever company {company!r} not found (404)")
    resp.raise_for_status()

    return parse_postings(resp.json(), entry)


def parse_postings(postings: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("token") or "Lever"
    out = []
    for p in postings or []:
        categories = p.get("categories") or {}
        out.append(normalize_posting(
            source="lever",
            company_name=company_name,
            external_id=p.get("id"),
            url=p.get("hostedUrl") or p.get("applyUrl"),
            title=p.get("text", ""),
            location=categories.get("location"),
            employment_type=categories.get("commitment"),
            posted_at=epoch_ms_to_iso(p.get("createdAt")),
            description=p.get("descriptionPlain") or html_to_text(p.get("description")),
            department=categories.get("team"),
            raw=p,
        ))
    return out
