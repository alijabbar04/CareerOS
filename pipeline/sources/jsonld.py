"""Generic schema.org JobPosting JSON-LD parser (research/D_job_sources.md
section 2): fetches a single posting URL and extracts the
`<script type="application/ld+json">` block most ATS job pages embed (verified
on Workday, Lever, Teamtailor, Pinpoint, Ashby, Reed, ICAEW, ACCA and
HSBC/Avature; Greenhouse and Workable do not carry it). Used for `--url`
(pasted postings) and as a universal fallback for any single job page.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting, to_int

LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Registry-shaped entry point (endpoint.url), kept symmetric with every
    other ats module even though pipeline.scan's --url flag calls fetch_url()
    directly instead of going through the registry."""
    endpoint = entry.get("endpoint") or {}
    url = endpoint.get("url")
    if not url:
        raise ValueError("jsonld entry missing endpoint.url")
    posting = fetch_url(url, company_hint=entry.get("company"))
    return [posting] if posting else []


def fetch_url(url: str, company_hint: str | None = None) -> dict[str, Any] | None:
    """Fetch one posting URL and return a single normalised posting dict, or
    None if no JobPosting JSON-LD block was found on the page."""
    resp = http.get(url, headers={"Accept": "text/html"})
    resp.raise_for_status()
    return parse_html(resp.text, url, company_hint)


def parse_html(html_text: str, url: str, company_hint: str | None = None) -> dict[str, Any] | None:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    job = _find_job_posting(html_text)
    if job is None:
        return None
    return _normalize(job, url, company_hint)


def _find_job_posting(page_html: str) -> dict[str, Any] | None:
    for match in LD_JSON_RE.finditer(page_html):
        try:
            data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        for block in data if isinstance(data, list) else [data]:
            if not isinstance(block, dict):
                continue
            if block.get("@type") == "JobPosting":
                return block
            for node in block.get("@graph") or []:
                if isinstance(node, dict) and node.get("@type") == "JobPosting":
                    return node
    return None


def _normalize(job: dict[str, Any], url: str, company_hint: str | None) -> dict[str, Any]:
    org = job.get("hiringOrganization")
    company_name = (org.get("name") if isinstance(org, dict) else None) or company_hint or "Unknown"
    employment_type = job.get("employmentType")
    if isinstance(employment_type, str) and employment_type.startswith("["):
        # Some sites (seen live on train.icaew.com) double-encode this as a JSON
        # array *string* (e.g. '["OTHER"]') rather than an actual JSON array.
        try:
            employment_type = json.loads(employment_type)
        except json.JSONDecodeError:
            pass
    if isinstance(employment_type, list):
        employment_type = ", ".join(str(e) for e in employment_type)
    identifier = job.get("identifier")
    external_id = (identifier.get("value") or None) if isinstance(identifier, dict) else identifier
    salary_min, salary_max, currency = _salary(job.get("baseSalary"))
    return normalize_posting(
        source="jsonld",
        company_name=company_name,
        external_id=external_id,
        url=url,
        title=job.get("title", ""),
        location=_location_text(job.get("jobLocation")),
        employment_type=employment_type,
        posted_at=job.get("datePosted"),
        closes_at=job.get("validThrough"),
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        description=html_to_text(job.get("description")),
        raw=job,
    )


def _location_text(job_location: Any) -> str | None:
    locations = job_location if isinstance(job_location, list) else [job_location]
    parts = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address") or {}
        if isinstance(address, dict):
            piece = ", ".join(filter(None, [
                address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry"),
            ]))
            if piece:
                parts.append(piece)
    return "; ".join(parts) or None


def _salary(base_salary: Any) -> tuple[int | None, int | None, str | None]:
    if not isinstance(base_salary, dict):
        return None, None, None
    currency = base_salary.get("currency")
    value = base_salary.get("value")
    if not isinstance(value, dict):
        return None, None, currency
    lo, hi, single = value.get("minValue"), value.get("maxValue"), value.get("value")
    if lo is None and hi is None and single is not None:
        lo = hi = single
    return to_int(lo), to_int(hi), currency
