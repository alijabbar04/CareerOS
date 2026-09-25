"""Recruitee offers API (research/D_job_sources.md section 1).

    GET https://{company}.recruitee.com/api/offers/

No auth. Reported (not live-verified) in research/D_job_sources.md; field
names below follow Recruitee's published offer object.
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
            raise ValueError("recruitee entry missing endpoint.company/url")
        url = f"https://{company}.recruitee.com/api/offers/"
    elif not url.startswith("http"):
        url = f"https://{url}"

    resp = http.get(url)
    resp.raise_for_status()
    offers = resp.json().get("offers", [])
    return parse_offers(offers, entry)


def parse_offers(offers: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("company") or "Recruitee"
    out = []
    for o in offers or []:
        out.append(normalize_posting(
            source="recruitee",
            company_name=company_name,
            external_id=o.get("id"),
            url=o.get("careers_url") or o.get("careers_apply_url"),
            title=o.get("title", ""),
            location=", ".join(filter(None, [o.get("city"), o.get("country")])) or None,
            remote=bool(o.get("remote")) if "remote" in o else None,
            employment_type=o.get("employment_type_code"),
            posted_at=o.get("published_at") or o.get("created_at"),
            department=o.get("department"),
            description=html_to_text(o.get("description")),
            raw=o,
        ))
    return out
