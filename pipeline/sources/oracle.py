"""Oracle Recruiting Cloud REST API (research/D_job_sources.md section 1).

    GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={site},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC

No auth. `site` defaults to CX_1001 (the value on every registry entry that
specifies one at all); it is the number visible in the employer's own careers
URL (`/CandidateExperience/en/sites/CX_1001`).
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import normalize_posting

DEFAULT_SITE = "CX_1001"
PAGE_SIZE = 25
# 60 * 25 = 1,500 postings safety cap. Comfortably covers every registry entry
# except JPMorgan (TotalJobsCount 7,209, live-confirmed 2026-09-22): JPMorgan is
# the registry's own priority-3 "long shot" bulge-bracket entry, so this
# deliberately does not chase its full count -- that would mean ~290 requests
# (about 5 minutes at the 1-req/second-per-host throttle) against one low-priority
# employer on every scan.
MAX_PAGES = 60


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    host = endpoint.get("host")
    if not host:
        raise ValueError("oracle entry missing endpoint.host")
    site = endpoint.get("site", DEFAULT_SITE)
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    out: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None  # pinned from the first page (see workday.py for why)
    for _ in range(MAX_PAGES):
        finder = f"findReqs;siteNumber={site},limit={PAGE_SIZE},offset={offset},sortBy=POSTING_DATES_DESC"
        resp = http.get(url, params={
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations",
            "finder": finder,
        })
        resp.raise_for_status()
        items = resp.json().get("items") or [{}]
        item = items[0]
        reqs = item.get("requisitionList") or []
        if total is None:
            total = item.get("TotalJobsCount", 0)
        out.extend(parse_requisitions(reqs, host, site, entry))
        offset += PAGE_SIZE
        if not reqs or offset >= total:
            break
    return out


def parse_requisitions(
    requisitions: list[dict[str, Any]], host: str, site: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    endpoint = entry.get("endpoint") or {}
    company_name = entry.get("company") or endpoint.get("host") or host
    out = []
    for r in requisitions or []:
        req_id = r.get("Id") or r.get("RequisitionId")
        url = (
            f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}"
            if req_id else None
        )
        out.append(normalize_posting(
            source="oracle",
            company_name=company_name,
            external_id=req_id,
            url=url,
            title=r.get("Title", ""),
            location=r.get("PrimaryLocation"),
            posted_at=r.get("PostedDate") or r.get("ExternalPostedStartDate"),
            closes_at=r.get("PostingEndDate"),
            employment_type=r.get("ContractType"),
            department=r.get("Department"),
            description=r.get("ShortDescriptionStr") or "",
            raw=r,
        ))
    return out
