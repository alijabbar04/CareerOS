"""Ashby job board API (research/D_job_sources.md section 1).

    GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true

No auth. `org` is usually the company slug but can be a domain-like string
(iwoca uses `iwoca.co.uk`). A wrong org returns 404.
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting, to_int


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    org = endpoint.get("org")
    if not org:
        raise ValueError("ashby entry missing endpoint.org")
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"

    resp = http.get(url, params={"includeCompensation": "true"})
    if resp.status_code == 404:
        raise ValueError(f"ashby org {org!r} not found (404)")
    resp.raise_for_status()

    jobs = resp.json().get("jobs", [])
    return parse_jobs(jobs, entry)


def parse_jobs(jobs: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("org") or "Ashby"
    out = []
    for j in jobs or []:
        salary_min, salary_max, currency = _compensation(j.get("compensation"))
        out.append(normalize_posting(
            source="ashby",
            company_name=company_name,
            external_id=j.get("id"),
            url=j.get("jobUrl") or j.get("applyUrl"),
            title=j.get("title", ""),
            location=j.get("location"),
            employment_type=j.get("employmentType"),
            posted_at=j.get("publishedAt"),
            description=html_to_text(j.get("descriptionHtml") or j.get("description")),
            department=j.get("department") or j.get("team"),
            salary_min=salary_min,
            salary_max=salary_max,
            currency=currency,
            raw=j,
        ))
    return out


def _compensation(comp: Any) -> tuple[int | None, int | None, str | None]:
    """Ashby's compensation object shape is not fully confirmed by live probing
    (research/D_job_sources.md only notes the field is present); this reads the
    common top-level min/max/currency keys if they exist and otherwise leaves
    salary unset rather than guessing at a nested structure."""
    if not isinstance(comp, dict):
        return None, None, None
    return to_int(comp.get("compensationMin") or comp.get("min")), \
        to_int(comp.get("compensationMax") or comp.get("max")), \
        comp.get("currencyCode") or comp.get("currency")
