"""Greenhouse Job Board API (research/D_job_sources.md section 1).

    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

No auth. `content=true` adds the HTML `content` field, `departments`, `offices`
and Greenhouse's AI-policy transparency fields (`ai_disclaimer`,
`include_ai_disclaimer`, `ai_opt_out_request_url`). A wrong token returns 404
(verified for iwoca, starlingbank, moneybox, checkoutcom -- see GOTCHAS.md).

Some employers (e.g. Man Group) are on the EU cluster (`job-boards.eu.greenhouse.io`)
rather than the default `boards-api.greenhouse.io`; registry entries may set
`endpoint.host` to override it.
"""
from __future__ import annotations

from typing import Any

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting

DEFAULT_HOST = "boards-api.greenhouse.io"


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    token = endpoint.get("token")
    if not token:
        raise ValueError("greenhouse entry missing endpoint.token")
    host = endpoint.get("host", DEFAULT_HOST)
    url = f"https://{host}/v1/boards/{token}/jobs"

    resp = http.get(url, params={"content": "true"})
    if resp.status_code == 404:
        raise ValueError(f"greenhouse token {token!r} not found (404) on host {host!r}")
    resp.raise_for_status()

    jobs = resp.json().get("jobs", [])
    return parse_jobs(jobs, entry)


def parse_jobs(jobs: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    company_name = entry.get("company") or (entry.get("endpoint") or {}).get("token", "Greenhouse")
    out = []
    for job in jobs:
        location = (job.get("location") or {}).get("name")
        departments = job.get("departments") or []
        department = departments[0].get("name") if departments else None
        ai_disclaimer = job.get("ai_disclaimer") or (
            f"AI disclaimer required (opt-out: {job['ai_opt_out_request_url']})"
            if job.get("include_ai_disclaimer") and job.get("ai_opt_out_request_url")
            else ("AI disclaimer required" if job.get("include_ai_disclaimer") else None)
        )
        out.append(normalize_posting(
            source="greenhouse",
            company_name=company_name,
            external_id=job.get("id"),
            url=job.get("absolute_url"),
            title=job.get("title", ""),
            location=location,
            posted_at=job.get("first_published") or job.get("updated_at"),
            closes_at=job.get("application_deadline"),
            description=html_to_text(job.get("content")),
            department=department,
            raw=job,
            ai_policy_hint=ai_disclaimer,
        ))
    return out
