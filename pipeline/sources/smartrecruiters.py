"""SmartRecruiters Posting API (research/D_job_sources.md section 1).

    GET https://api.smartrecruiters.com/v1/companies/{companyId}/postings
        ?country=gb&limit=100&offset=0

No auth, paginated by offset/totalFound. `companyId` is taken from
`endpoint.company_id` if given, else the last path segment of `endpoint.url`
(e.g. `careers.smartrecruiters.com/EvelynPartners` -> `EvelynPartners`) --
research/D_job_sources.md flags that guess as unconfirmed for Firm D.
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting, to_int

PAGE_SIZE = 100
MAX_PAGES = 20  # 20 * 100 = 2,000 postings safety cap


def _company_id(endpoint: dict[str, Any]) -> str | None:
    if endpoint.get("company_id"):
        return endpoint["company_id"]
    url = (endpoint.get("url") or "").strip()
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1] or None


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    company_id = _company_id(endpoint)
    if not company_id:
        raise ValueError("smartrecruiters entry missing endpoint.company_id/url")
    base = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"

    out: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None  # pinned from the first page (see workday.py for why)
    for _ in range(MAX_PAGES):
        resp = http.get(base, params={"country": "gb", "limit": PAGE_SIZE, "offset": offset})
        if resp.status_code == 404:
            raise ValueError(f"smartrecruiters company {company_id!r} not found (404)")
        resp.raise_for_status()
        data = resp.json()
        items = data.get("content", [])
        if total is None:
            total = data.get("totalFound", 0)
        out.extend(parse_postings(items, entry))
        offset += PAGE_SIZE
        if not items or offset >= total:
            break
    return out


def parse_postings(items: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_id = _company_id(endpoint)
    company_name = entry.get("company") or company_id or "SmartRecruiters"
    out = []
    for p in items or []:
        # Defensive isinstance checks throughout this loop: pinpoint.py's `department`
        # field turned out to be a plain string for one employer and a {"id","name"}
        # reference object for another (see GOTCHAS.md, 2026-09-22), so nothing here
        # assumes a nested field's shape is the same across every SmartRecruiters company.
        loc = p.get("location")
        location = (
            ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")])) or None
            if isinstance(loc, dict) else (loc or None)
        )
        posting_id = p.get("id")
        job_ad = p.get("jobAd")
        sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
        sections = sections if isinstance(sections, dict) else {}
        description = " ".join(
            html_to_text((sections.get(key) or {}).get("text", ""))
            for key in ("jobDescription", "qualifications")
            if isinstance(sections.get(key), dict)
        ).strip()
        out.append(normalize_posting(
            source="smartrecruiters",
            company_name=company_name,
            external_id=posting_id,
            url=f"https://jobs.smartrecruiters.com/{company_id}/{posting_id}" if company_id and posting_id else None,
            title=p.get("name", ""),
            location=location,
            remote=bool(loc.get("remote")) if "remote" in loc else None,
            posted_at=p.get("releasedDate"),
            department=(p.get("department") or {}).get("label"),
            description=description,
            salary_min=to_int((p.get("compensation") or {}).get("min")),
            salary_max=to_int((p.get("compensation") or {}).get("max")),
            raw=p,
        ))
    return out
