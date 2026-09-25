"""Reed Jobseeker API (research/D_job_sources.md section 2).

    GET https://www.reed.co.uk/api/1.0/search

Free API key used as the HTTP basic-auth username (empty password); up to 100
results per request, no documented rate limit.

Key comes from .env (REED_API_KEY, see .env.example). If it is not set,
fetch() raises FetchSkipped rather than failing the whole scan.
"""
from __future__ import annotations

import os
from typing import Any

from pipeline import config, http
from pipeline.sources.common import FetchSkipped, normalize_posting, to_int

URL = "https://www.reed.co.uk/api/1.0/search"
KEYWORDS = "graduate trainee analyst apprentice"
RESULTS_PER_PAGE = 100
MAX_PAGES = 5


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    config.load_env()
    api_key = os.environ.get("REED_API_KEY")
    if not api_key:
        raise FetchSkipped("REED_API_KEY not set in .env; see .env.example")

    out: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        resp = http.get(
            URL,
            params={
                "keywords": KEYWORDS,
                "locationName": "London",
                "resultsToTake": RESULTS_PER_PAGE,
                "resultsToSkip": page * RESULTS_PER_PAGE,
            },
            auth=(api_key, ""),
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            break
        out.extend(parse_results(results, entry))
        if len(results) < RESULTS_PER_PAGE:
            break
    return out


def parse_results(results: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    out = []
    for j in results or []:
        out.append(normalize_posting(
            source="reed",
            company_name=j.get("employerName") or "Reed",
            external_id=j.get("jobId"),
            url=j.get("jobUrl") or j.get("externalUrl"),
            title=j.get("jobTitle", ""),
            location=j.get("locationName"),
            posted_at=j.get("date"),
            closes_at=j.get("expirationDate"),
            salary_min=to_int(j.get("minimumSalary")),
            salary_max=to_int(j.get("maximumSalary")),
            currency="GBP",
            description=j.get("jobDescription", ""),
            raw=j,
        ))
    return out
