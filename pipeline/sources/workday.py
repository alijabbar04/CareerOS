"""Workday `cxs` endpoint (research/D_job_sources.md section 1).

    POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

No auth. `limit` above 20 returns HTTP 400, so PAGE_SIZE is fixed at 20 and
results are paginated by offset. A wrong `{site}` name returns HTTP 422, not
404 (see GOTCHAS.md) -- valid names are listed in the tenant's robots.txt
sitemap.

A single registry entry may list more than one site (e.g. LBG's
`Graduate_careers` and `LBG_Careers`). fetch() polls every listed site and
keeps going if one of them 422s, recording a warning so pipeline.scan can fold
it into that source's source_health note without losing the sites that did
work. If every site fails, fetch() raises instead (nothing to return).
"""
from __future__ import annotations

import warnings
from typing import Any

from pipeline import http
from pipeline.sources.common import normalize_posting

PAGE_SIZE = 20
# 100 * 20 = 2,000 postings/site safety cap. Live-confirmed (2026-09-22): PwC's
# Global_Campus_Careers alone carries 1,500 and Barclays 826, both genuinely
# relevant priority-2 employers, so a lower cap silently truncated real coverage.
MAX_PAGES_PER_SITE = 100
DEFAULT_WD = "wd3"


class _SiteError(RuntimeError):
    pass


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    tenant = endpoint.get("tenant")
    if not tenant:
        raise ValueError("workday entry missing endpoint.tenant")
    wd = endpoint.get("wd", DEFAULT_WD)
    sites = endpoint.get("sites") or []
    if not sites:
        raise ValueError(f"workday tenant {tenant!r} has no endpoint.sites configured")

    out: list[dict[str, Any]] = []
    ok_sites = 0
    for site in sites:
        try:
            out.extend(_fetch_site(tenant, wd, site, entry))
            ok_sites += 1
        except _SiteError as exc:
            warnings.warn(str(exc), RuntimeWarning, stacklevel=2)
    if ok_sites == 0:
        raise ValueError(f"every site failed for workday tenant {tenant!r}: {sites}")
    return out


def _fetch_site(tenant: str, wd: str, site: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    out: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None  # pinned from the first page only -- see below
    for _ in range(MAX_PAGES_PER_SITE):
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        resp = http.post(url, json=body, headers={"Accept": "application/json"})
        if resp.status_code == 422:
            raise _SiteError(
                f"workday site {site!r} invalid for tenant {tenant!r} (422) -- "
                f"check https://{tenant}.{wd}.myworkdayjobs.com/robots.txt for valid site names"
            )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        # Live-confirmed on aviva/External (2026-09-22): `total` is correct on the
        # first page (offset 0) but comes back 0 on every later page even while
        # jobPostings keeps returning full pages -- trusting a later page's total
        # over the first one truncated Aviva's 135 postings down to 40. Pin it from
        # the first page and otherwise rely on an empty page to end the loop.
        if total is None:
            total = data.get("total", 0)
        out.extend(parse_job_postings(postings, tenant, wd, site, entry))
        offset += PAGE_SIZE
        if not postings or offset >= total:
            break
    return out


def parse_job_postings(
    postings: list[dict[str, Any]], tenant: str, wd: str, site: str, entry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    company_name = entry.get("company") or tenant
    out = []
    for p in postings or []:
        path = p.get("externalPath", "")
        url = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{path}" if path else None
        bullets = p.get("bulletFields") or []
        external_id = bullets[0] if bullets else (path or None)
        out.append(normalize_posting(
            source="workday",
            company_name=company_name,
            external_id=external_id,
            url=url,
            title=p.get("title", ""),
            location=p.get("locationsText"),
            # Workday's list endpoint gives a relative human string here (e.g.
            # "Posted 30+ Days Ago"), not an ISO date; only the per-posting
            # detail endpoint has a real postedOn, and fetching that for every
            # posting would multiply the request count well past what "poll at
            # most hourly, be polite" allows.
            posted_at=p.get("postedOn"),
            description="",
            raw=p,
        ))
    return out
