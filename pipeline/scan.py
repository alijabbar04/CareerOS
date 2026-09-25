"""T-011: poll every registry entry with a working fetcher and store postings.

    python -m pipeline.scan                          # full run against data/registry.yaml
    python -m pipeline.scan --dry-run                 # fetch and report, write nothing
    python -m pipeline.scan --only "Monzo"             # one company
    python -m pipeline.scan --ats greenhouse           # one ats
    python -m pipeline.scan --force                    # ignore the 55-minute per-source gate
    python -m pipeline.scan --url https://example.com/jobs/123   # single pasted URL, source="manual"

For each entry whose `ats` has a fetcher (pipeline.sources.get_fetcher; entries
on email-alerts/unknown/oleeo/other HTML-only vendors are skipped with a note),
this: waits out pipeline.http's per-source 55-minute gate (unless --force),
calls the fetcher, applies `title_regex`/`location_regex` (case-insensitive)
to decide `matched`, upserts every posting it got (matched or not) with
source_match 1/0, updates source_health, and never lets one entry's failure stop
the rest. A posting's title_regex match is checked against its title; its
location_regex match is checked against location *or* description, because
several sources (RSS feeds in particular) do not return a structured location
field even though the description almost always names one.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import warnings
from dataclasses import dataclass
from typing import Any

from pipeline import config, db, http, registry, sources
from pipeline.sources import jsonld


@dataclass
class _Result:
    company: str
    ats: str | None
    status: str  # "ok" | "fail" | "skip"
    fetched: int = 0
    matched: int = 0
    new: int = 0
    note: str | None = None


def _is_matched(posting: dict[str, Any], title_re: re.Pattern | None, location_re: re.Pattern | None) -> bool:
    if title_re is None or location_re is None:
        return False
    if not title_re.search(posting.get("title") or ""):
        return False
    haystack = " ".join(filter(None, [posting.get("location"), posting.get("description")]))
    return bool(location_re.search(haystack))


def _posting_exists(conn: sqlite3.Connection, canonical_url: str) -> bool:
    row = conn.execute("SELECT 1 FROM postings WHERE canonical_url = ?", (canonical_url,)).fetchone()
    return row is not None


def _posting_row(posting: dict[str, Any], company_id: int, tracks: list[str], source_match: int) -> dict[str, Any]:
    """Map a normalised posting dict (pipeline.sources.common.normalize_posting
    shape) onto exactly the columns pipeline.db.upsert_posting can write.
    company_name has no column of its own (postings.company_id is a foreign
    key, resolved via db.upsert_company beforehand); fields left out here
    (track from company_name resolution aside) either have no column
    (fit scores, fingerprint, ...) or are intentionally left for T-012.

    None values are dropped rather than sent through: upsert_posting's SET
    clause only touches columns present in the dict, so on a repeat sighting a
    field this fetch could not find (e.g. no salary this time) does not
    clobber a value a previous sighting did find.
    """
    row = {
        "company_id": company_id,
        "source": posting.get("source"),
        "external_id": posting.get("external_id"),
        "url": posting.get("url"),
        "title": posting.get("title") or "",
        "location": posting.get("location"),
        "remote": posting.get("remote"),
        "track": ", ".join(tracks) if tracks else None,
        "salary_min": posting.get("salary_min"),
        "salary_max": posting.get("salary_max"),
        "currency": posting.get("currency") or "GBP",
        "description": posting.get("description") or "",
        "employment_type": posting.get("employment_type"),
        "department": posting.get("department"),
        "raw_json": posting.get("raw_json"),
        "class_year_rule": posting.get("class_year_rule"),
        "ai_policy_hint": posting.get("ai_policy_hint"),
        "source_match": source_match,
        "posted_at": posting.get("posted_at"),
        "closes_at": posting.get("closes_at"),
    }
    row = {k: v for k, v in row.items() if v is not None}
    row["canonical_url"] = posting["canonical_url"]  # always keep, even though never None
    return row


def _store_posting(conn: sqlite3.Connection, posting: dict[str, Any], tracks: list[str], source_match: int) -> None:
    company_id = db.upsert_company(posting["company_name"], conn=conn)
    db.upsert_posting(_posting_row(posting, company_id, tracks, source_match), conn=conn)


def _process_entry(entry: dict[str, Any], conn: sqlite3.Connection, dry_run: bool, force: bool) -> _Result:
    company = entry.get("company", "?")
    ats = entry.get("ats")

    fetcher, skip_reason = sources.get_fetcher(entry)
    if fetcher is None:
        return _Result(company, ats, status="skip", note=skip_reason)

    if not http.due_for_poll(company, conn, force=force):
        return _Result(company, ats, status="skip", note="polled in the last 55 minutes; use --force")

    with warnings.catch_warnings(record=True) as caught:
        # Only capture the RuntimeWarnings fetchers themselves emit for a partial
        # failure (e.g. workday.py's "site 422, skipping it but keeping the rest"),
        # not incidental DeprecationWarnings etc. from library code a fetcher calls.
        warnings.simplefilter("ignore")
        warnings.filterwarnings("always", category=RuntimeWarning)
        try:
            postings = fetcher(entry)
        except sources.FetchSkipped as exc:
            if not dry_run:
                db.record_source_health(company, ok=True, note=str(exc), conn=conn)
            return _Result(company, ats, status="skip", note=str(exc))
        except Exception as exc:  # noqa: BLE001 -- one bad source must never stop the run
            note = f"{type(exc).__name__}: {exc}"[:500]
            if not dry_run:
                db.record_source_health(company, ok=False, note=note, conn=conn)
            return _Result(company, ats, status="fail", note=note)

    warn_note = "; ".join(str(w.message) for w in caught) if caught else None

    title_re = re.compile(entry.get("title_regex", ""), re.IGNORECASE) if entry.get("title_regex") else None
    location_re = re.compile(entry.get("location_regex", ""), re.IGNORECASE) if entry.get("location_regex") else None
    tracks = entry.get("tracks") or []

    matched = new = storage_errors = 0
    for posting in postings:
        is_matched = _is_matched(posting, title_re, location_re)
        matched += int(is_matched)
        already_existed = _posting_exists(conn, posting["canonical_url"])
        if not dry_run:
            # A single malformed posting (an unexpected field shape a fetcher's own
            # normaliser did not catch) must not cost the rest of this source's
            # postings, let alone the rest of the run -- see GOTCHAS.md, 2026-09-22:
            # one dict-valued field crashed the whole scan before this try/except.
            try:
                _store_posting(conn, posting, tracks, source_match=1 if is_matched else 0)
            except Exception as exc:  # noqa: BLE001
                storage_errors += 1
                print(f"  ! failed to store {posting.get('canonical_url')!r}: {type(exc).__name__}: {exc}")
                continue
        if not already_existed:
            new += 1

    if storage_errors:
        storage_note = f"{storage_errors} posting(s) failed to store"
        warn_note = f"{warn_note}; {storage_note}" if warn_note else storage_note

    if not dry_run:
        db.record_source_health(company, ok=True, note=warn_note, conn=conn)

    return _Result(company, ats, status="ok", fetched=len(postings), matched=matched, new=new, note=warn_note)


def _scan_single_url(url: str, conn: sqlite3.Connection, dry_run: bool) -> None:
    posting = jsonld.fetch_url(url)
    if posting is None:
        print(f"No JobPosting JSON-LD found at {url}")
        return
    posting["source"] = "manual"
    if dry_run:
        print(f"[dry-run] would store: {posting['title']!r} @ {posting['company_name']} -> {posting['canonical_url']}")
        return
    _store_posting(conn, posting, tracks=[], source_match=0)
    print(f"Stored: {posting['title']!r} @ {posting['company_name']} -> {posting['canonical_url']}")


def _print_summary(results: list[_Result], dry_run: bool) -> None:
    cols = f"{'company':32.32s} {'ats':16.16s} {'status':6s} {'fetched':>7s} {'matched':>7s} {'new':>5s}  note"
    if dry_run:
        print("[DRY RUN -- nothing written]")
    print(cols)
    print("-" * len(cols))
    total_fetched = total_matched = total_new = 0
    ok = fail = skip = 0
    for r in results:
        print(f"{r.company:32.32s} {(r.ats or ''):16.16s} {r.status:6s} {r.fetched:7d} {r.matched:7d} {r.new:5d}  {(r.note or '')[:100]}")
        total_fetched += r.fetched
        total_matched += r.matched
        total_new += r.new
        ok += r.status == "ok"
        fail += r.status == "fail"
        skip += r.status == "skip"
    print("-" * len(cols))
    print(
        f"{len(results)} entries: {ok} ok, {fail} failed, {skip} skipped -- "
        f"{total_fetched} fetched, {total_matched} matched, {total_new} new"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.scan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", metavar="COMPANY", help="only scan the registry entry with this exact company name")
    parser.add_argument("--ats", help="only scan registry entries with this ats value")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, but write nothing to the database")
    parser.add_argument("--force", action="store_true", help="ignore the 55-minute per-source minimum interval")
    parser.add_argument("--url", metavar="URL", help="fetch a single posting via JobPosting JSON-LD and upsert it with source='manual'")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conn = db.connect()
    db.migrate(conn)

    if args.url:
        _scan_single_url(args.url, conn, dry_run=args.dry_run)
        return

    entries = registry.load_registry()
    registry.validate(entries)  # fail loudly on a bad hand-edit rather than mis-poll silently

    if args.only:
        entries = [e for e in entries if e.get("company") == args.only]
        if not entries:
            print(f"No registry entry named {args.only!r}")
            return
    if args.ats:
        entries = [e for e in entries if e.get("ats") == args.ats]

    results = [_process_entry(e, conn, args.dry_run, args.force) for e in entries]
    _print_summary(results, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
