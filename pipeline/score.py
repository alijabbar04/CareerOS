"""T-012 deterministic fit scoring, optional Ollama review, and shortlist export.

Typical use:

    python -m pipeline.score                 # rules + dedupe + Markdown shortlist
    python -m pipeline.score --models        # add two local qwen3 passes to the top survivors
    python -m pipeline.score --dry-run       # calculate and print, write nothing

The first model pass sees only the posting and judges basic role relevance. The
second sees a small set of confirmed claims (tier safe, sensitivity free, portable
categories only) and must cite their IDs in a structured fit rubric. Model output is advisory: deterministic hard filters
always win, unknown claim IDs are rejected, and no secret or vault value is
ever included in either prompt.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from rapidfuzz.fuzz import token_set_ratio

from pipeline import config, db, dedupe, registry, settings

SCORE_VERSION = "rules-v1"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
_STRONG_ENTRY_RE = re.compile(
    r"\b(?:graduate|trainee|junior|entry[ -]level|assistant|apprentice|intern)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_ENTRY_RE = re.compile(
    r"\b(?:analyst|associate)\b",
    re.IGNORECASE,
)
_SENIOR_RE = re.compile(
    r"\b(?:senior|lead|manager|director|head of|principal|vice president|vp|chief|partner)\b",
    re.IGNORECASE,
)
_UK_RE = re.compile(
    r"\b(?:london|united kingdom|u\.?k\.?|england|scotland|wales|northern ireland|remote)\b",
    re.IGNORECASE,
)
_NO_SPONSOR_RE = re.compile(
    r"\b(?:no|not)\s+(?:visa\s+)?sponsorship|unable to (?:offer|provide) sponsorship|right to work .* without sponsorship",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_YEAR_CONTEXT_RE = re.compile(r"graduat(?:e|es|ing|ion)|class of|final.year|penultimate.year", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z][a-z0-9+#.]{2,}")
_STOP_WORDS = {
    "and", "the", "for", "with", "that", "this", "from", "your", "you", "our",
    "are", "will", "have", "has", "into", "role", "work", "working", "team", "their",
    "job", "who", "but", "not", "all", "can", "use", "using", "skills", "experience",
}
_BASELINE_CLAIMS = {"C-0002", "C-0019", "C-0020"}
_PORTABLE_CLAIM_CATEGORIES = {"achievement", "education", "experience", "project", "skill", "story"}


@dataclass(frozen=True)
class RuleResult:
    eligible: bool
    score: int
    reasons: tuple[str, ...]
    missing_requirements: tuple[str, ...] = ()
    disposition: str = "ignored"


def _row_get(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _class_year_excludes(rule: str, graduation_year: int | None) -> bool:
    if not rule or graduation_year is None or not _YEAR_CONTEXT_RE.search(rule):
        return False
    explicit_exclusion = re.search(
        rf"(?:{graduation_year}\s+graduate.{{0,40}}(?:falls? outside|not eligible|excluded)|"
        rf"(?:excludes?|outside).{{0,40}}{graduation_year}\s+graduate)",
        rule,
        re.IGNORECASE,
    )
    if explicit_exclusion:
        return True
    years = {int(value) for value in _YEAR_RE.findall(rule)}
    # Only reject explicit year lists. Text such as "applications open in 2026"
    # is not a class-year rule even if a source put it in this field.
    return bool(years) and graduation_year not in years


def _track_points(track_text: str, ordered_tracks: list[str]) -> tuple[int, str | None]:
    posting_tracks = {part.strip() for part in (track_text or "").split(",") if part.strip()}
    # Aggregator rows initially carry the registry's broad search set (all
    # tracks). Treat that as unknown rather than awarding the first-track bonus.
    if len(posting_tracks) > 3:
        return 0, None
    for index, track in enumerate(ordered_tracks):
        if track in posting_tracks:
            return max(2, 20 - index * 2), track
    return 0, None


def _applicable_registry_class_year_rule(title: str, rule: str | None) -> str | None:
    """Use a registry restriction only when it names this role before a colon.

    Registry notes often describe one programme at an employer that also has
    unrestricted non-programme jobs. Applying the employer note globally would
    wrongly reject those other roles.
    """
    if not rule or ":" not in rule:
        return None
    role_label = rule.split(":", 1)[0]
    role_label = re.sub(r"\brole\b", " ", role_label, flags=re.IGNORECASE)
    if not _YEAR_CONTEXT_RE.search(role_label):
        return None
    similarity = token_set_ratio(dedupe.normalise_text(role_label), dedupe.normalise_text(title))
    return rule if similarity >= 90 else None


def rule_score(
    posting: sqlite3.Row | dict[str, Any],
    *,
    today: date,
    ordered_tracks: list[str],
    company_priority: int | None,
    graduation_year: int | None,
    salary_floor_gbp: int | None,
    requires_sponsorship: bool | None,
    applied_company_ids: set[int],
) -> RuleResult:
    """Score one posting using only explicit, inspectable facts and settings."""
    title = str(_row_get(posting, "title", ""))
    description = str(_row_get(posting, "description", ""))
    location = str(_row_get(posting, "location", ""))
    company_id = int(_row_get(posting, "company_id", 0))
    reasons: list[str] = []
    missing: list[str] = []

    if int(_row_get(posting, "source_match", 0)) != 1:
        return RuleResult(False, 0, ("did not pass the registry title-and-location match",))
    if _row_get(posting, "duplicate_of"):
        return RuleResult(False, 0, (f"duplicate of posting {_row_get(posting, 'duplicate_of')}",))
    closes_at = _parse_date(_row_get(posting, "closes_at"))
    if closes_at and closes_at < today:
        return RuleResult(False, 0, (f"deadline passed on {closes_at.isoformat()}",), disposition="expired")
    if company_id in applied_company_ids:
        return RuleResult(False, 0, ("application to this employer already exists in the current cycle",))
    class_rule = str(_row_get(posting, "class_year_rule", ""))
    if _class_year_excludes(class_rule, graduation_year):
        return RuleResult(
            False,
            0,
            (f"explicit class-year wording excludes a {graduation_year} graduate",),
            ("ask the recruiter whether earlier graduates are eligible",),
        )
    if _SENIOR_RE.search(title) and not _STRONG_ENTRY_RE.search(title):
        return RuleResult(False, 0, ("title explicitly indicates a senior role",))
    if requires_sponsorship is True and _NO_SPONSOR_RE.search(f"{title}\n{description}"):
        return RuleResult(False, 0, ("posting explicitly says sponsorship is unavailable",))
    salary_max = _row_get(posting, "salary_max")
    currency = str(_row_get(posting, "currency", "GBP")).upper()
    if salary_floor_gbp is not None and currency == "GBP" and salary_max is not None and int(salary_max) < salary_floor_gbp:
        return RuleResult(False, 0, (f"maximum stated salary is below the configured GBP {salary_floor_gbp:,} floor",))

    location_haystack = f"{location}\n{description[:5000]}"
    if not _UK_RE.search(location_haystack):
        return RuleResult(False, 0, ("no UK or remote location evidence was found",))

    score = 25
    reasons.append("passed the registry title-and-location match")

    if _STRONG_ENTRY_RE.search(title):
        score += 20
        reasons.append("title has an explicit entry-level signal")
    elif _AMBIGUOUS_ENTRY_RE.search(title):
        score += 8
        reasons.append("title has an analyst/associate signal but seniority is not conclusive")
        missing.append("analyst/associate seniority needs confirmation from the description")
    else:
        missing.append("seniority is not explicit in the title")

    if re.search(r"\blondon\b", location_haystack, re.IGNORECASE):
        score += 15
        reasons.append("London is named")
    elif _UK_RE.search(location_haystack):
        score += 10
        reasons.append("UK or remote-UK wording is present")
    else:
        missing.append("UK location needs manual confirmation")

    track_points, matched_track = _track_points(str(_row_get(posting, "track", "")), ordered_tracks)
    if matched_track:
        score += track_points
        reasons.append(f"matches configured track {matched_track}")
    else:
        missing.append("track mapping is absent")

    if company_priority in (1, 2, 3):
        priority_points = {1: 15, 2: 10, 3: 5}[company_priority]
        score += priority_points
        reasons.append(f"registry employer priority is {company_priority}")

    if closes_at:
        days_left = (closes_at - today).days
        score += 5 if days_left <= 14 else 3
        reasons.append(f"deadline is {closes_at.isoformat()} ({days_left} days left)")
    else:
        missing.append("closing date is not published")

    if salary_max is not None or _row_get(posting, "salary_min") is not None:
        score += 2
        reasons.append("salary is disclosed")
    else:
        missing.append("salary is not published")

    if requires_sponsorship is None:
        missing.append("sponsorship requirement is not configured; no sponsorship filter applied")
    if salary_floor_gbp is None:
        missing.append("salary floor is not configured; no salary filter applied")
    return RuleResult(True, min(100, score), tuple(reasons), tuple(missing))


class OllamaClient:
    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL, url: str = OLLAMA_URL, timeout: int = 180) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout

    def generate_json(self, prompt: str) -> dict[str, Any]:
        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "think": False,
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        parsed = json.loads(payload["response"])
        if not isinstance(parsed, dict):
            raise ValueError("model returned JSON that is not an object")
        return parsed


def local_relevance(client: OllamaClient, posting: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    prompt = f"""You are the first-pass job relevance filter for a UK Economics graduate seeking entry-level work in accountancy, finance, public finance, fintech operations, bank operations/risk, economic consulting, or product operations.

Judge only the job posting below. Do not infer facts about the candidate. Return exactly one JSON object with keys: relevant (boolean), score (integer 0-100), reasons (array of at most 3 short strings), missing_requirements (array of short strings). A role that is senior, outside the UK, expired, or explicitly restricted to a different graduation year is not relevant.

Title: {_row_get(posting, 'title', '')}
Company: {_row_get(posting, 'company_name', '')}
Location: {_row_get(posting, 'location', '')}
Class-year wording: {_row_get(posting, 'class_year_rule', '')}
Description:
{str(_row_get(posting, 'description', ''))[:7000]}
"""
    result = client.generate_json(prompt)
    return {
        "relevant": bool(result.get("relevant")),
        "score": max(0, min(100, int(result.get("score", 0)))),
        "reasons": [str(item)[:240] for item in result.get("reasons", [])][:3],
        "missing_requirements": [str(item)[:240] for item in result.get("missing_requirements", [])][:8],
    }


def _claim_tokens(text: str) -> set[str]:
    return {word for word in _WORD_RE.findall(text.casefold()) if word not in _STOP_WORDS}


def _load_safe_claims(conn: sqlite3.Connection) -> list[dict[str, str]]:
    try:
        rows = conn.execute(
            """SELECT id, text, subject, category FROM claims
               WHERE status = 'confirmed' AND COALESCE(tier, 'safe') = 'safe'
                 AND COALESCE(sensitivity, 'free') = 'free'"""
        ).fetchall()
        if rows:
            return [
                {"id": row["id"], "text": row["text"], "subject": row["subject"] or ""}
                for row in rows
                if row["category"] in _PORTABLE_CLAIM_CATEGORIES
            ]
    except sqlite3.OperationalError:
        pass

    claims: list[dict[str, str]] = []
    claims_path = config.BRAIN_DIR / "claims.jsonl"
    if not claims_path.exists():
        return claims
    for line in claims_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if (
            record.get("status") == "confirmed"
            and record.get("tier", "safe") == "safe"
            and record.get("sensitivity", "free") == "free"
            and record.get("category") in _PORTABLE_CLAIM_CATEGORIES
        ):
            claims.append({"id": record["id"], "text": record["text"], "subject": record.get("subject") or ""})
    return claims


def retrieve_claims(posting: sqlite3.Row | dict[str, Any], claims: list[dict[str, str]], limit: int = 16) -> list[dict[str, str]]:
    query = _claim_tokens(
        " ".join(
            (
                str(_row_get(posting, "title", "")),
                str(_row_get(posting, "track", "")),
                str(_row_get(posting, "description", ""))[:9000],
            )
        )
    )
    ranked: list[tuple[float, dict[str, str]]] = []
    for claim in claims:
        tokens = _claim_tokens(f"{claim['subject']} {claim['text']}")
        overlap = len(query & tokens)
        score = overlap / math.sqrt(max(1, len(tokens))) if overlap else 0.0
        if score or claim["id"] in _BASELINE_CLAIMS:
            ranked.append((score + (0.01 if claim["id"] in _BASELINE_CLAIMS else 0.0), claim))
    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    selected = [claim for _, claim in ranked[:limit]]
    # Ensure baseline education is available even when keyword-heavy experience claims dominate.
    by_id = {claim["id"]: claim for claim in claims}
    for claim_id in sorted(_BASELINE_CLAIMS):
        if claim_id in by_id and all(item["id"] != claim_id for item in selected):
            selected.append(by_id[claim_id])
    return selected[: limit + len(_BASELINE_CLAIMS)]


def evidence_rubric(
    client: OllamaClient,
    posting: sqlite3.Row | dict[str, Any],
    claims: list[dict[str, str]],
) -> dict[str, Any]:
    claim_lines = "\n".join(f"- {claim['id']}: {claim['text']}" for claim in claims)
    prompt = f"""Score candidate fit for this job using only the confirmed claims supplied below. Do not invent or strengthen a claim. Return exactly one JSON object with keys: fit (integer 0-100), reasons (array of at most 4 short strings), missing_requirements (array), cited_claims (array of claim IDs). Every positive candidate-fit reason must be supported by at least one cited claim ID. Job facts do not need claim IDs.

JOB
Title: {_row_get(posting, 'title', '')}
Company: {_row_get(posting, 'company_name', '')}
Location: {_row_get(posting, 'location', '')}
Description:
{str(_row_get(posting, 'description', ''))[:9000]}

CONFIRMED SAFE CLAIMS
{claim_lines}
"""
    result = client.generate_json(prompt)
    allowed = {claim["id"] for claim in claims}
    cited = [str(item) for item in result.get("cited_claims", [])]
    invalid = sorted(set(cited) - allowed)
    if invalid:
        raise ValueError(f"model cited claim IDs not supplied to it: {', '.join(invalid)}")
    if not cited:
        raise ValueError("model rubric returned no claim citations")
    return {
        "fit": max(0, min(100, int(result.get("fit", 0)))),
        "reasons": [str(item)[:300] for item in result.get("reasons", [])][:4],
        "missing_requirements": [str(item)[:300] for item in result.get("missing_requirements", [])][:10],
        "cited_claims": sorted(set(cited)),
    }


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return [str(value)]


def final_score(row: sqlite3.Row | dict[str, Any]) -> int:
    rule = int(_row_get(row, "rule_score", 0))
    model_is_current = bool(
        _row_get(row, "fingerprint")
        and _row_get(row, "model_fingerprint") == _row_get(row, "fingerprint")
    )
    local = _row_get(row, "local_model_score") if model_is_current else None
    rubric = _row_get(row, "llm_score") if model_is_current else None
    if rubric is not None and local is not None:
        return round(rule * 0.35 + int(local) * 0.20 + int(rubric) * 0.45)
    if rubric is not None:
        return round(rule * 0.45 + int(rubric) * 0.55)
    if local is not None:
        return round(rule * 0.70 + int(local) * 0.30)
    return rule


def model_score_is_current(row: sqlite3.Row | dict[str, Any]) -> bool:
    return bool(
        _row_get(row, "fingerprint")
        and _row_get(row, "model_fingerprint") == _row_get(row, "fingerprint")
        and _row_get(row, "local_model_score") is not None
    )


def _current_cycle_companies(conn: sqlite3.Connection, cycle_start: str) -> set[int]:
    return {
        int(row["company_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT company_id FROM applications
            WHERE (submitted_at IS NOT NULL AND date(submitted_at) >= date(?))
               OR (submitted_at IS NULL AND status IN ('draft','ready-for-review','approved') AND date(created_at) >= date(?))
            """,
            (cycle_start, cycle_start),
        )
    }


def _registry_map() -> dict[str, dict[str, Any]]:
    entries = registry.load_registry()
    registry.validate(entries)
    return {entry["company"].casefold(): entry for entry in entries}


def score_postings(
    conn: sqlite3.Connection,
    *,
    today: date,
    active_settings: settings.Settings,
    use_models: bool = False,
    model_limit: int = 25,
    model: str = DEFAULT_OLLAMA_MODEL,
    dry_run: bool = False,
) -> dict[str, int]:
    registry_by_company = _registry_map()
    ordered_tracks = list(active_settings.get("tracks", default=[]) or [])
    graduation_year = active_settings.get("scoring", "candidate_graduation_year", default=None)
    graduation_year = int(graduation_year) if graduation_year is not None else None
    salary_floor = active_settings.get("scoring", "salary_floor_gbp", default=None)
    salary_floor = int(salary_floor) if salary_floor is not None else None
    requires_sponsorship = active_settings.get("scoring", "requires_sponsorship", default=None)
    cycle_start = str(active_settings.get("caps", "cycle_start", default=f"{today.year}-01-01"))
    applied_company_ids = _current_cycle_companies(conn, cycle_start)

    rows = conn.execute(
        """
        SELECT p.*, c.name AS company_name
        FROM postings p JOIN companies c ON c.id = p.company_id
        WHERE COALESCE(p.source_match, 0) = 1
          AND p.duplicate_of IS NULL
          AND p.status IN ('new','shortlisted')
        ORDER BY p.id
        """
    ).fetchall()
    eligible_rows: list[tuple[sqlite3.Row, RuleResult]] = []
    filtered = expired = 0
    for row in rows:
        entry = registry_by_company.get(str(row["company_name"]).casefold(), {})
        row_for_rules = dict(row)
        if entry.get("tracks"):
            # Prefer the named employer's curated tracks over an aggregator
            # source entry whose search configuration deliberately spans all tracks.
            row_for_rules["track"] = ", ".join(entry["tracks"])
        registry_class_rule = _applicable_registry_class_year_rule(
            str(row_for_rules.get("title") or ""), entry.get("class_year_rule")
        )
        if not row_for_rules.get("class_year_rule") and registry_class_rule:
            # Some APIs omit eligibility prose even though the curated employer
            # record has an explicit, role-specific class-year restriction.
            row_for_rules["class_year_rule"] = registry_class_rule
        result = rule_score(
            row_for_rules,
            today=today,
            ordered_tracks=ordered_tracks,
            company_priority=entry.get("priority"),
            graduation_year=graduation_year,
            salary_floor_gbp=salary_floor,
            requires_sponsorship=requires_sponsorship,
            applied_company_ids=applied_company_ids,
        )
        if result.eligible:
            eligible_rows.append((row, result))
        else:
            filtered += 1
            expired += result.disposition == "expired"
        if not dry_run:
            new_status = row["status"] if result.eligible else result.disposition
            conn.execute(
                """
                UPDATE postings
                SET rule_score = ?, rule_reasons = ?, missing_requirements = ?,
                    score_version = ?, scored_at = datetime('now'), status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    result.score,
                    json.dumps(result.reasons, ensure_ascii=False),
                    json.dumps(result.missing_requirements, ensure_ascii=False),
                    SCORE_VERSION,
                    new_status,
                    row["id"],
                ),
            )

    modelled = rubric_scored = model_failures = 0
    if use_models and eligible_rows:
        client = OllamaClient(model=model)
        safe_claims = _load_safe_claims(conn)
        for row, result in sorted(eligible_rows, key=lambda item: (-item[1].score, int(item[0]["id"])))[:model_limit]:
            try:
                local = local_relevance(client, row)
                modelled += 1
                print(
                    f"posting {row['id']}: local relevance {local['score']}/100 "
                    f"({'keep' if local['relevant'] else 'drop'})",
                    flush=True,
                )
                rubric: dict[str, Any] | None = None
                selected_claims: list[dict[str, str]] = []
                if local["relevant"]:
                    selected_claims = retrieve_claims(row, safe_claims)
                    rubric = evidence_rubric(client, row, selected_claims)
                    rubric_scored += 1
                    print(
                        f"posting {row['id']}: evidence rubric {rubric['fit']}/100 "
                        f"({len(rubric['cited_claims'])} claim IDs)",
                        flush=True,
                    )
                if not dry_run:
                    combined_missing = list(result.missing_requirements) + local["missing_requirements"]
                    current_fingerprint = row["fingerprint"] or dedupe.fingerprint(
                        row["title"], row["company_name"], row["description"]
                    )
                    conn.execute(
                        """
                        UPDATE postings
                        SET local_model_score = ?, local_model_reasons = ?,
                            llm_score = ?, llm_reasons = ?, missing_requirements = ?, cited_claims = ?,
                            score_version = ?, fingerprint = COALESCE(fingerprint, ?), model_fingerprint = ?,
                            scored_at = datetime('now'), updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (
                            local["score"],
                            json.dumps(local["reasons"], ensure_ascii=False),
                            rubric["fit"] if rubric else None,
                            json.dumps(rubric["reasons"], ensure_ascii=False) if rubric else None,
                            json.dumps(combined_missing + (rubric["missing_requirements"] if rubric else []), ensure_ascii=False),
                            json.dumps(rubric["cited_claims"]) if rubric else None,
                            f"{SCORE_VERSION}+ollama:{model}",
                            current_fingerprint,
                            current_fingerprint,
                            row["id"],
                        ),
                    )
            except (requests.exceptions.RequestException, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                model_failures += 1
                print(f"model pass failed for posting {row['id']}: {type(exc).__name__}: {exc}", flush=True)

    if not dry_run:
        conn.commit()
        threshold = int(active_settings.get("scoring", "shortlist_threshold", default=60))
        scored_rows = conn.execute(
            "SELECT * FROM postings WHERE source_match = 1 AND duplicate_of IS NULL AND status IN ('new','shortlisted')"
        ).fetchall()
        for row in scored_rows:
            status = "shortlisted" if final_score(row) >= threshold else "new"
            conn.execute("UPDATE postings SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, row["id"]))
        conn.commit()

    return {
        "considered": len(rows),
        "eligible": len(eligible_rows),
        "filtered": filtered,
        "expired": expired,
        "modelled": modelled,
        "rubric_scored": rubric_scored,
        "model_failures": model_failures,
    }


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def export_shortlist(
    conn: sqlite3.Connection,
    *,
    run_date: date,
    active_settings: settings.Settings,
    path: Path | None = None,
) -> Path:
    limit = int(active_settings.get("scoring", "shortlist_limit", default=25))
    registry_by_company = _registry_map()
    rows = conn.execute(
        """
        SELECT p.*, c.name AS company_name
        FROM postings p JOIN companies c ON c.id = p.company_id
        WHERE p.status = 'shortlisted' AND p.duplicate_of IS NULL
        """
    ).fetchall()
    ranked = sorted(
        rows,
        key=lambda row: (
            -final_score(row),
            -int(model_score_is_current(row)),
            registry_by_company.get(str(row["company_name"]).casefold(), {}).get("priority", 99),
            str(row["company_name"]).casefold(),
            str(row["title"]).casefold(),
        ),
    )[:limit]
    output = path or (config.DATA_DIR / f"shortlist-{run_date.isoformat()}.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Daily shortlist — {run_date.isoformat()}",
        "",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "Scores combine deterministic rules with any completed local relevance and evidence-rubric passes. "
        "Claim IDs are accepted only when they resolve to confirmed, safe ledger claims. Duplicate rows are excluded.",
        "",
    ]
    if not ranked:
        lines.extend(("No postings currently meet the shortlist threshold.", ""))
    for index, row in enumerate(ranked, start=1):
        score = final_score(row)
        reasons = _json_list(row["llm_reasons"]) or _json_list(row["local_model_reasons"]) or _json_list(row["rule_reasons"])
        missing = _json_list(row["missing_requirements"])
        cited = _json_list(row["cited_claims"])
        lines.extend(
            (
                f"## {index}. {_md(row['title'])} — {_md(row['company_name'])} ({score}/100)",
                "",
                f"- Location: {_md(row['location']) or 'Not published'}",
                f"- Track: {_md(row['track']) or 'Not mapped'}",
                f"- Closing date: {_md(row['closes_at']) or 'Not published'}",
                f"- Source: [{_md(row['source'])}]({_md(row['canonical_url'])})",
                f"- Fit reasons: {'; '.join(_md(item) for item in reasons) or 'No reasons recorded'}",
                f"- Missing/check: {'; '.join(_md(item) for item in missing) or 'None recorded'}",
                f"- Confirmed claims cited: {', '.join(cited) if cited else 'Rule-only score; model rubric not run'}",
                "",
            )
        )
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.score", description=__doc__)
    parser.add_argument("--models", action="store_true", help="run the two Ollama passes on top rule survivors")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help="Ollama model name")
    parser.add_argument("--model-limit", type=int, default=10, help="maximum postings sent through model passes")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="run date, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="calculate and print without database or file writes")
    parser.add_argument("--skip-dedupe", action="store_true", help="do not run dedupe first")
    parser.add_argument("--output", type=Path, help="shortlist path (default: data/shortlist-YYYY-MM-DD.md)")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    conn = db.connect()
    db.migrate(conn)
    active_settings = settings.Settings()
    if not args.skip_dedupe:
        print("dedupe:", json.dumps(dedupe.dedupe(conn, dry_run=args.dry_run), sort_keys=True))
    result = score_postings(
        conn,
        today=args.date,
        active_settings=active_settings,
        use_models=args.models,
        model_limit=max(0, args.model_limit),
        model=args.model,
        dry_run=args.dry_run,
    )
    print("scoring:", json.dumps(result, sort_keys=True))
    if not args.dry_run:
        output = export_shortlist(conn, run_date=args.date, active_settings=active_settings, path=args.output)
        print(f"shortlist: {output}")


if __name__ == "__main__":
    main()
