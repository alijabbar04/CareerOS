"""T-011: shared helpers every pipeline/sources/*.py fetcher module builds on.

Kept in its own module (rather than in pipeline/sources/__init__.py) so the ATS
modules can `from pipeline.sources.common import normalize_posting` without a
circular import: __init__.py imports the ATS modules themselves to build the
fetcher registry, so it cannot be the thing they import from.

`normalize_posting()` is the "common Posting normalisation helper": every ats
module calls it once per posting so the dicts pipeline.scan receives always have
the same shape (research/D_job_sources.md section 4):

    source, external_id, canonical_url, url, company_name, title, location,
    remote (bool or None), employment_type, posted_at, closes_at, salary_min,
    salary_max, currency, description (plain text, may be empty), department,
    raw_json (string, truncated to 20k), class_year_rule, ai_policy_hint

first_seen/last_seen are not here -- pipeline.db.upsert_posting stamps those.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

Posting = dict[str, Any]

RAW_JSON_MAX_CHARS = 20_000

_DROP_QUERY_PREFIXES = ("utm_",)
_DROP_QUERY_KEYS = {"gclid", "fbclid"}

CLASS_YEAR_RE = re.compile(
    r"graduat(?:e|ing)\s+in\s+20\d{2}"
    r"|class\s+of\s+20\d{2}"
    r"|20\d{2}\s*/\s*20\d{2}\s+graduates?",
    re.IGNORECASE,
)

AI_POLICY_RE = re.compile(
    r"do\s+not\s+use\s+(?:any\s+)?ai\b"
    r"|no\s+(?:use\s+of\s+)?ai\s+tools?\b"
    r"|without\s+(?:the\s+)?(?:use\s+of\s+)?ai\b"
    r"|ai\s+tools?\b"
    r"|artificial\s+intelligence\s+tools?\b"
    r"|generative\s+ai\b"
    r"|chatgpt\b",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_CLOSE_RE = re.compile(r"</\s*(p|div|li|br|h[1-6]|tr)\s*>", re.IGNORECASE)


class FetchSkipped(RuntimeError):
    """Raised by a fetcher when it cannot run for a reason outside the registry
    entry's control (typically a missing API key). pipeline.scan records the
    message in source_health without treating it as a source failure.
    """


# ---------------------------------------------------------------------------
# Text cleanup
# ---------------------------------------------------------------------------

def html_to_text(value: str | None) -> str:
    """Strip an ATS's HTML-formatted description down to plain text: newline at
    block-level closing tags, entities unescaped, remaining tags dropped,
    whitespace collapsed. Good enough for storage and regex scanning; not a
    full HTML renderer (no BeautifulSoup/lxml in this project's dependencies).
    """
    if not value:
        return ""
    text = _BLOCK_CLOSE_RE.sub("\n", value)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def epoch_ms_to_iso(value: Any) -> str | None:
    """Lever (and some others) give timestamps as epoch milliseconds."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# URL canonicalisation (research/D_job_sources.md section 4 dedupe rule)
# ---------------------------------------------------------------------------

def canonicalize_url(url: str) -> str:
    """Lower-case the host, strip utm_*/gclid/fbclid query parameters and sort
    what remains. Used as the canonical_url upsert key so the same posting
    fetched twice (or found through two sources) updates one row rather than
    creating a duplicate.
    """
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_DROP_QUERY_PREFIXES) and k.lower() not in _DROP_QUERY_KEYS
    ]
    query = urlencode(sorted(query_pairs))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, query, ""))


# ---------------------------------------------------------------------------
# class_year_rule / ai_policy_hint extraction
# ---------------------------------------------------------------------------

def extract_class_year_rule(text: str) -> str | None:
    """Find phrasing like 'graduating in 2027', 'class of 2026' or '2026/2027
    graduates' in `text` (normally title + description)."""
    if not text:
        return None
    m = CLASS_YEAR_RE.search(text)
    return m.group(0) if m else None


def extract_ai_policy_hint(text: str, context_chars: int = 80) -> str | None:
    """Find phrases such as 'do not use AI' / 'AI tools' in `text` and return a
    short snippet around the match. Callers with an explicit signal (Greenhouse's
    ai_disclaimer field) should pass it straight to normalize_posting instead."""
    if not text:
        return None
    m = AI_POLICY_RE.search(text)
    if not m:
        return None
    start, end = max(0, m.start() - context_chars), min(len(text), m.end() + context_chars)
    return " ".join(text[start:end].split())


def dump_raw_json(obj: Any) -> str:
    text = json.dumps(obj, default=str, ensure_ascii=False)
    return text[:RAW_JSON_MAX_CHARS]


def _scalarize(value: Any) -> Any:
    """Defend against an ATS returning a nested reference object where a plain
    scalar was expected. Live-confirmed (2026-09-22): Pinpoint's `department`
    is a plain string for some employers (Firm C) and a `{"id": ..., "name":
    ...}` object for others (Kreston Reeves) -- and sqlite3 cannot bind a dict
    or list, so one such field previously crashed the entire scan run instead
    of just that one posting. Every ats module should still normalise its own
    known shapes explicitly (clearer and cheaper than relying on this); this is
    the last-resort net for a shape nobody has seen live yet.
    """
    if isinstance(value, dict):
        for key in ("name", "label", "value", "text"):
            if isinstance(value.get(key), str):
                return value[key]
        return str(value) if value else None
    if isinstance(value, list):
        flat = [_scalarize(v) for v in value]
        return ", ".join(str(v) for v in flat if v) or None
    return value


# ---------------------------------------------------------------------------
# The normalisation helper itself
# ---------------------------------------------------------------------------

def normalize_posting(
    *,
    source: str,
    company_name: str,
    title: str,
    url: str | None,
    external_id: Any = None,
    canonical_url: str | None = None,
    location: str | None = None,
    remote: bool | None = None,
    employment_type: str | None = None,
    posted_at: str | None = None,
    closes_at: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    currency: str | None = "GBP",
    description: str = "",
    department: str | None = None,
    raw: Any = None,
    ai_policy_hint: str | None = None,
) -> Posting:
    """Build one normalised posting dict from whatever fields an ats module
    extracted. Computes canonical_url (from `canonical_url` or else `url`),
    class_year_rule and ai_policy_hint (from title/description, unless
    `ai_policy_hint` is given explicitly) and truncates raw_json to 20k chars.

    `url` may be None only when the caller genuinely has nothing better than a
    missing/placeholder link (canonicalize_url then just normalises whatever
    empty-ish string it is given); every fetcher should try hard not to hit
    that case.
    """
    description = description or ""
    text_for_rules = f"{title}\n{description}"
    return {
        "source": source,
        "external_id": str(external_id) if external_id is not None else None,
        "canonical_url": canonicalize_url(canonical_url or url or ""),
        "url": url,
        "company_name": company_name,
        "title": title,
        "location": _scalarize(location),
        "remote": remote,
        "employment_type": _scalarize(employment_type),
        "posted_at": _scalarize(posted_at),
        "closes_at": _scalarize(closes_at),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "currency": _scalarize(currency),
        "description": description,
        "department": _scalarize(department),
        "raw_json": dump_raw_json(raw) if raw is not None else None,
        "class_year_rule": extract_class_year_rule(text_for_rules),
        "ai_policy_hint": ai_policy_hint or extract_ai_policy_hint(description),
    }
