"""T-011: fetchers for the ATS platforms and aggregators in data/registry.yaml.

    from pipeline import sources
    fetcher, skip_reason = sources.get_fetcher(entry)   # entry is one registry.yaml row
    postings = fetcher(entry)                            # -> list[dict], see sources.common

Every module below exposes `fetch(entry: dict) -> list[dict]` and normalises
through `sources.common.normalize_posting` (re-exported here), so every
posting dict pipeline.scan receives has the same shape regardless of source.

`ATS_FETCHERS` maps a registry `ats` value to its fetcher. Two ats values are
not simple 1:1 lookups:
  * "rss" / "successfactors-rss" / "avature-rss" are all plain RSS/Atom feeds
    once you have the URL, so all three point at rss.fetch.
  * "api" is shared by Adzuna and Reed (both literally `ats: api` in the
    registry, distinguished only by endpoint.url), so get_fetcher() sniffs the
    host instead of doing a plain dict lookup.
ats values with no fetcher here (email-alerts, unknown, oleeo, taleo, icims,
radancy, beamery, pageup, kallidus, hireserve, peoplehr, salesforce, networx,
self-hosted) are HTML-only or read-only-by-design; pipeline.scan skips them
with a note rather than guessing at an endpoint.
"""
from __future__ import annotations

from typing import Any, Callable

from pipeline.sources import (
    adzuna,
    ashby,
    greenhouse,
    jsonld,
    lever,
    oracle,
    pinpoint,
    recruitee,
    reed,
    rss,
    smartrecruiters,
    teamtailor,
    workable,
    workday,
)
from pipeline.sources.common import (
    FetchSkipped,
    Posting,
    canonicalize_url,
    normalize_posting,
)

__all__ = [
    "FetchSkipped",
    "Posting",
    "canonicalize_url",
    "normalize_posting",
    "ATS_FETCHERS",
    "get_fetcher",
]

Fetcher = Callable[[dict[str, Any]], list[dict[str, Any]]]

ATS_FETCHERS: dict[str, Fetcher] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workable": workable.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "pinpoint": pinpoint.fetch,
    "teamtailor": teamtailor.fetch,
    "recruitee": recruitee.fetch,
    "workday": workday.fetch,
    "oracle": oracle.fetch,
    "rss": rss.fetch,
    "successfactors-rss": rss.fetch,
    "avature-rss": rss.fetch,
}

_API_HOST_FETCHERS: dict[str, Fetcher] = {
    "adzuna.com": adzuna.fetch,
    "reed.co.uk": reed.fetch,
}


def get_fetcher(entry: dict[str, Any]) -> tuple[Fetcher | None, str | None]:
    """Resolve the fetch() function for a registry entry.

    Returns (fetcher, None) when there is one, or (None, reason) when this
    entry should be skipped -- either because its `ats` has no fetcher at all,
    or (for the shared "api" ats) its endpoint does not look like a known
    aggregator.
    """
    ats = entry.get("ats")
    if ats == "api":
        url = (entry.get("endpoint") or {}).get("url", "")
        for host, fetcher in _API_HOST_FETCHERS.items():
            if host in url:
                return fetcher, None
        return None, f"ats 'api' with an unrecognised endpoint ({url!r})"

    fetcher = ATS_FETCHERS.get(ats)
    if fetcher is None:
        return None, f"no fetcher for ats {ats!r} (HTML-only, email-alerts or unknown)"
    return fetcher, None
