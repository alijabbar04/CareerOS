"""Adzuna UK Jobs API (research/D_job_sources.md section 2).

    GET https://api.adzuna.com/v1/api/jobs/gb/search/{page}

Free app_id/app_key (25 requests/minute, 250/day, 1,000/week, 2,500/month).
Results must be labelled "Jobs by Adzuna" wherever displayed; pipeline.scan's
summary table names the source, and any UI built on this data downstream must
keep that attribution.

Keys come from .env (ADZUNA_APP_ID, ADZUNA_APP_KEY, see .env.example). If they
are not set, fetch() raises FetchSkipped rather than failing the whole scan.
"""
from __future__ import annotations

import os
from typing import Any

from pipeline import config, http
from pipeline.sources.common import FetchSkipped, normalize_posting, to_int

BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search"
KEYWORDS = "graduate trainee analyst apprentice"
CATEGORY = "accounting-finance-jobs"
RESULTS_PER_PAGE = 50
MAX_PAGES = 5  # keeps one scan well inside the free daily quota (250/day)


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    config.load_env()
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise FetchSkipped("ADZUNA_APP_ID/ADZUNA_APP_KEY not set in .env; see .env.example")

    # Adzuna's `what` requires every word; `what_or` matches any. Run a few any-of queries and dedupe by id.
    queries = [
        ("graduate trainee apprentice", CATEGORY),
        ("analyst assistant associate", CATEGORY),
        ("operations analyst implementation", None),
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for what_or, category in queries:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what_or": what_or,
                "where": "London",
                "results_per_page": RESULTS_PER_PAGE,
                "content-type": "application/json",
            }
            if category:
                params["category"] = category
            resp = http.get(f"{BASE_URL}/{page}", params=params)
            resp.raise_for_status()
            results = [r for r in resp.json().get("results", []) if str(r.get("id")) not in seen]
            seen.update(str(r.get("id")) for r in results)
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
        company = (j.get("company") or {}).get("display_name")
        location = (j.get("location") or {}).get("display_name")
        out.append(normalize_posting(
            source="adzuna",
            company_name=company or "Adzuna",
            external_id=j.get("id"),
            url=j.get("redirect_url"),
            title=j.get("title", ""),
            location=location,
            posted_at=j.get("created"),
            salary_min=to_int(j.get("salary_min")),
            salary_max=to_int(j.get("salary_max")),
            description=j.get("description", ""),
            raw=j,
        ))
    return out
