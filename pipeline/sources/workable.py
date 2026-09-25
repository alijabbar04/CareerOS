"""Workable widget accounts API (research/D_job_sources.md section 1).

    GET https://apply.workable.com/api/v1/widget/accounts/{slug}

No auth. Verified fields: title, shortcode, url, application_url, published_on,
department, city -- no description, so `description` is left empty (the
normalised schema explicitly allows that).

Live-confirmed (2026-09-22, Starling Bank): a role open at several offices
comes back as one entry *per office*, all sharing the same shortcode and apply
url -- e.g. "Android Engineer" appeared 4 times (Manchester, Cardiff,
Southampton, London), identical in every field except `city`. Left ungrouped,
each shares one canonical_url and pipeline.scan upserts them in sequence, so
whichever office happened to be last in the list silently wins the stored
`location` (and therefore the location_regex match) -- a London posting could
lose its match entirely if a non-UK office variant landed last. parse_jobs()
groups by shortcode and joins the offices into one `location` string instead.
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import normalize_posting


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    slug = endpoint.get("slug")
    if not slug:
        raise ValueError("workable entry missing endpoint.slug")
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"

    resp = http.get(url)
    if resp.status_code == 404:
        raise ValueError(f"workable slug {slug!r} not found (404)")
    resp.raise_for_status()

    jobs = resp.json().get("jobs", [])
    return parse_jobs(jobs, entry)


def parse_jobs(jobs: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("slug") or "Workable"

    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for j in jobs or []:
        key = j.get("shortcode") or j.get("url") or j.get("title", "")
        if key not in grouped:
            grouped[key] = {**j, "_cities": []}
            order.append(key)
        city = j.get("city") or j.get("location")
        if city and city not in grouped[key]["_cities"]:
            grouped[key]["_cities"].append(city)

    out = []
    for key in order:
        j = grouped[key]
        location = "; ".join(j["_cities"]) if j["_cities"] else (j.get("city") or j.get("location"))
        out.append(normalize_posting(
            source="workable",
            company_name=company_name,
            external_id=j.get("shortcode"),
            url=j.get("url") or j.get("application_url"),
            title=j.get("title", ""),
            location=location,
            employment_type=j.get("employment_type"),
            posted_at=j.get("published_on"),
            department=j.get("department"),
            description="",
            raw=j,
        ))
    return out
