"""Teamtailor JSON Feed (research/D_job_sources.md section 1).

    GET https://{company}.teamtailor.com/jobs.json

No auth. Standard JSON Feed (jsonfeed.org) format: {"items": [...]}, each item
carrying an embedded `_jobposting` schema.org-style object alongside the feed's
own id/url/title/content_html/date_published.
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    company = endpoint.get("company") or endpoint.get("slug")
    url = endpoint.get("url")
    if not url:
        if not company:
            raise ValueError("teamtailor entry missing endpoint.company/url")
        url = f"https://{company}.teamtailor.com/jobs.json"
    elif not url.startswith("http"):
        url = f"https://{url}"

    resp = http.get(url)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return parse_items(items, entry)


def parse_items(items: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("company") or "Teamtailor"
    out = []
    for item in items or []:
        jp = item.get("_jobposting") or {}
        location_text = _location_text(jp.get("jobLocation"))
        out.append(normalize_posting(
            source="teamtailor",
            company_name=company_name,
            external_id=item.get("id"),
            url=item.get("url"),
            title=item.get("title") or jp.get("title", ""),
            location=location_text,
            posted_at=item.get("date_published") or jp.get("datePosted"),
            closes_at=jp.get("validThrough"),
            employment_type=jp.get("employmentType"),
            description=html_to_text(item.get("content_html")) or (item.get("content_text") or ""),
            raw=item,
        ))
    return out


def _location_text(job_location: Any) -> str | None:
    if not isinstance(job_location, dict):
        return None
    address = job_location.get("address") or {}
    if not isinstance(address, dict):
        return None
    return ", ".join(filter(None, [address.get("addressLocality"), address.get("addressCountry")])) or None
