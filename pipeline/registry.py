"""T-008: load and validate data/registry.yaml, the curated list of target employers
the discovery pipeline (T-011 onwards) polls.

    python -m pipeline.registry              # validate data/registry.yaml, print summary counts
    python -m pipeline.registry <other.yaml>  # validate a different file

Other pipeline modules should call ``load_registry()`` then ``validate()`` before
polling any endpoint, so a bad hand-edit to the YAML fails loudly instead of
silently skipping (or mis-polling) an employer.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from pipeline import config

ALLOWED_ATS = {
    "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "pinpoint",
    "teamtailor", "recruitee", "workday", "oracle", "successfactors-rss",
    "avature-rss", "oleeo", "taleo", "icims", "radancy", "beamery", "pageup",
    "kallidus", "hireserve", "peoplehr", "salesforce", "networx",
    "self-hosted", "rss", "api", "email-alerts", "unknown",
}

ALLOWED_TRACKS = {
    "1-aca-acca-training-contracts",
    "2-economic-consulting",
    "3-wealth-and-asset-management",
    "5-bank-ops-and-risk",
    "6-industry-and-insurer-finance",
    "7-fintech-ops-and-analyst",
    "8-public-finance",
    "9-product-engineering-ai-assisted",
}

ALLOWED_VERIFIED = {"Verified", "UNVERIFIED"}
ALLOWED_PRIORITY = {1, 2, 3}

# Every field the schema (see TASKS.md T-008) requires on each entry.
REQUIRED_FIELDS = [
    "company", "ats", "endpoint", "tracks", "title_regex", "location_regex",
    "ai_policy", "apply_limit", "class_year_rule", "academic_requirement",
    "deadlines", "priority", "verified", "source", "notes",
]

# Fields that must be strings when present (endpoint, tracks, priority have their own checks).
STRING_FIELDS = [
    "company", "title_regex", "location_regex", "ai_policy", "apply_limit",
    "class_year_rule", "academic_requirement", "deadlines", "verified",
    "source", "notes",
]

DEFAULT_PATH = config.DATA_DIR / "registry.yaml"


def load_registry(path: str | Path | None = None) -> list[dict]:
    """Load the employer registry YAML and return the raw list of entry dicts.

    Does not validate; call ``validate()`` on the result before relying on it.
    """
    p = Path(path) if path is not None else DEFAULT_PATH
    if not p.exists():
        raise FileNotFoundError(f"registry not found: {p}")
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict) or "employers" not in doc:
        raise ValueError(f"{p}: expected a top-level mapping with an 'employers' key")
    entries = doc["employers"]
    if not isinstance(entries, list):
        raise ValueError(f"{p}: 'employers' must be a list")
    return entries


def validate(entries: list[dict]) -> None:
    """Validate registry entries against the T-008 schema.

    Raises ValueError with every problem found (not just the first), so a
    single run of ``python -m pipeline.registry`` surfaces the whole list of
    fixes needed after a hand-edit.
    """
    problems: list[str] = []
    company_counts: dict[str, int] = {}

    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f"entry #{i}: not a mapping (got {type(e).__name__})")
            continue

        company = e.get("company")
        label = company if isinstance(company, str) and company else f"entry #{i}"

        for field in REQUIRED_FIELDS:
            if field not in e:
                problems.append(f"{label}: missing required field '{field}'")

        if "company" in e:
            if not isinstance(company, str) or not company.strip():
                problems.append(f"{label}: 'company' must be a non-empty string")
            else:
                company_counts[company] = company_counts.get(company, 0) + 1

        if "ats" in e and e["ats"] not in ALLOWED_ATS:
            problems.append(f"{label}: bad ats value {e['ats']!r} (allowed: {sorted(ALLOWED_ATS)})")

        if "endpoint" in e and not isinstance(e["endpoint"], dict):
            problems.append(f"{label}: 'endpoint' must be a mapping, got {type(e['endpoint']).__name__}")

        if "tracks" in e:
            tracks = e["tracks"]
            if not isinstance(tracks, list) or not tracks:
                problems.append(f"{label}: 'tracks' must be a non-empty list")
            else:
                for t in tracks:
                    if t not in ALLOWED_TRACKS:
                        problems.append(f"{label}: bad track value {t!r} (allowed: {sorted(ALLOWED_TRACKS)})")

        if "priority" in e and e["priority"] not in ALLOWED_PRIORITY:
            problems.append(f"{label}: priority {e['priority']!r} not in 1-3")

        for field in STRING_FIELDS:
            if field in e and not isinstance(e[field], str):
                problems.append(f"{label}: '{field}' must be a string, got {type(e[field]).__name__}")

        if "verified" in e and isinstance(e["verified"], str) and e["verified"] not in ALLOWED_VERIFIED:
            problems.append(f"{label}: verified {e['verified']!r} not one of {sorted(ALLOWED_VERIFIED)}")

        for field in ("title_regex", "location_regex"):
            val = e.get(field)
            if isinstance(val, str):
                try:
                    re.compile(val)
                except re.error as exc:
                    problems.append(f"{label}: '{field}' is not a valid regex ({exc})")

    for company, count in sorted(company_counts.items()):
        if count > 1:
            problems.append(f"duplicate company {company!r} appears {count} times")

    if problems:
        raise ValueError(
            f"{len(problems)} problem(s) in registry:\n" + "\n".join(f"  - {p}" for p in problems)
        )


def _counts(entries: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        values = e.get(field)
        values = values if isinstance(values, list) else [values]
        for v in values:
            key = str(v)
            out[key] = out.get(key, 0) + 1
    return out


def _print_counts(title: str, counts: dict[str, int]) -> None:
    print(f"\n{title}:")
    for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {key:32s} {n}")


if __name__ == "__main__":
    registry_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        registry_entries = load_registry(registry_path)
        validate(registry_entries)
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        sys.exit(1)

    print(f"OK: {len(registry_entries)} employers validated ({registry_path or DEFAULT_PATH})")
    _print_counts("By ATS", _counts(registry_entries, "ats"))
    _print_counts("By priority", _counts(registry_entries, "priority"))
    _print_counts("By verified", _counts(registry_entries, "verified"))
