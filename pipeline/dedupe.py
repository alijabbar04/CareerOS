"""T-012 posting deduplication.

The decision rule comes from research/C_tech_stack.md section 5: mark two
records as duplicates only when at least two of these agree:

1. stable URL/job identity;
2. RapidFuzz title-and-company similarity >= 90;
3. description-vector cosine >= 0.92.

The checked-in implementation deliberately has no model download. It uses a
deterministic feature-hashed text vector, stored as a float32 SQLite BLOB. That
keeps the pipeline local and runnable today; the vector producer can later be
replaced by EmbeddingGemma without changing the schema or comparison code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

import numpy as np
from rapidfuzz.fuzz import token_set_ratio

from pipeline import config, db

FUZZY_THRESHOLD = 90.0
COSINE_THRESHOLD = 0.92
EMBEDDING_DIMENSIONS = 384
_TOKEN_RE = re.compile(r"[a-z0-9+#.]{2,}")
_TITLE_NOISE_RE = re.compile(
    r"\b(?:m/f/d|f/m/d|all genders|fixed term|full time|part time|hybrid|remote)\b",
    re.IGNORECASE,
)
_JOB_QUERY_KEYS = {"gh_jid", "jobid", "job_id", "jid", "reqid", "requisitionid"}


@dataclass(frozen=True)
class DuplicateDecision:
    duplicate: bool
    signals: tuple[str, ...]
    fuzzy_score: float
    cosine: float


def normalise_text(value: str | None) -> str:
    value = _TITLE_NOISE_RE.sub(" ", value or "")
    return " ".join(_TOKEN_RE.findall(value.casefold()))


def title_company_key(title: str | None, company: str | None) -> str:
    return f"{normalise_text(title)} | {normalise_text(company)}"


def fingerprint(title: str | None, company: str | None, description: str | None) -> str:
    """Stable content fingerprint for diagnostics, not a duplicate decision alone."""
    content = "\n".join((title_company_key(title, company), normalise_text(description)))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def text_embedding(text: str | None, dimensions: int = EMBEDDING_DIMENSIONS) -> np.ndarray:
    """Return a deterministic, unit-length local feature-hashed text vector.

    Unigrams and adjacent bigrams make near-identical job descriptions land
    close together while avoiding a heavyweight model or network request.
    """
    tokens = _TOKEN_RE.findall((text or "").casefold())
    vector = np.zeros(dimensions, dtype=np.float32)
    if not tokens:
        return vector
    features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "little")
        index = raw % dimensions
        sign = -1.0 if raw & (1 << 63) else 1.0
        vector[index] += sign
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def embedding_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def embedding_from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    vector = np.frombuffer(blob, dtype=np.float32)
    return vector if vector.size else None


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.size != right.size or not left.size:
        return 0.0
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator else 0.0


def url_identity(posting: dict[str, Any] | sqlite3.Row) -> str | None:
    """Extract a stable job identity without treating a shared board URL as one job."""
    source = normalise_text(posting["source"] if "source" in posting.keys() else None)
    external_id = str(posting["external_id"] or "").strip() if "external_id" in posting.keys() else ""
    if source and external_id:
        return f"{source}:{external_id.casefold()}"

    url = posting["canonical_url"] if "canonical_url" in posting.keys() else None
    if not url:
        return None
    parts = urlsplit(str(url))
    query = dict(parse_qsl(parts.query, keep_blank_values=False))
    for key, value in query.items():
        if key.casefold() in _JOB_QUERY_KEYS and value:
            return f"{parts.netloc.casefold()}:{value.casefold()}"
    path_parts = [p for p in parts.path.casefold().split("/") if p]
    if path_parts and re.search(r"\d", path_parts[-1]):
        return f"{parts.netloc.casefold()}:{path_parts[-1]}"
    return str(url).casefold().rstrip("/")


def compare(
    left: dict[str, Any] | sqlite3.Row,
    right: dict[str, Any] | sqlite3.Row,
    left_vector: np.ndarray | None = None,
    right_vector: np.ndarray | None = None,
) -> DuplicateDecision:
    left_key = title_company_key(left["title"], left["company_name"])
    right_key = title_company_key(right["title"], right["company_name"])
    fuzzy = float(token_set_ratio(left_key, right_key))
    left_vector = left_vector if left_vector is not None else text_embedding(left["description"])
    right_vector = right_vector if right_vector is not None else text_embedding(right["description"])
    cosine = cosine_similarity(left_vector, right_vector)

    signals: list[str] = []
    left_url_id, right_url_id = url_identity(left), url_identity(right)
    if left_url_id and left_url_id == right_url_id:
        signals.append("url_identity")
    if fuzzy >= FUZZY_THRESHOLD:
        signals.append("title_company")
    if cosine >= COSINE_THRESHOLD:
        signals.append("description_embedding")
    return DuplicateDecision(len(signals) >= 2, tuple(signals), fuzzy, cosine)


class _UnionFind:
    def __init__(self, ids: Iterable[int]) -> None:
        self.parent = {item: item for item in ids}

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        lroot, rroot = self.find(left), self.find(right)
        if lroot != rroot:
            self.parent[max(lroot, rroot)] = min(lroot, rroot)


def _canonical_rank(row: sqlite3.Row) -> tuple[int, int, int, int]:
    """Prefer applied/referenced records, then UK/London matches, then older IDs."""
    applied = int(row["has_application"] or row["status"] == "applied")
    location = (row["location"] or "").casefold()
    preferred_location = 2 if "london" in location else int(
        any(term in location for term in ("united kingdom", " uk", "remote"))
    )
    source_match = int(row["source_match"] or 0)
    return (-applied, -preferred_location, -source_match, int(row["id"]))


def dedupe(conn: sqlite3.Connection, matched_only: bool = True, dry_run: bool = False) -> dict[str, int]:
    where = "WHERE COALESCE(p.source_match, 0) = 1" if matched_only else ""
    rows = conn.execute(
        f"""
        SELECT p.*, c.name AS company_name,
               EXISTS(SELECT 1 FROM applications a WHERE a.posting_id = p.id) AS has_application
        FROM postings p JOIN companies c ON c.id = p.company_id
        {where}
        ORDER BY c.name COLLATE NOCASE, p.id
        """
    ).fetchall()
    vectors: dict[int, np.ndarray] = {}
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    uf = _UnionFind(int(row["id"]) for row in rows)
    decisions: dict[tuple[int, int], DuplicateDecision] = {}

    for row in rows:
        vector = embedding_from_blob(row["embedding"])
        if vector is None or vector.size != EMBEDDING_DIMENSIONS:
            vector = text_embedding(row["description"])
        vectors[int(row["id"])] = vector
        groups[normalise_text(row["company_name"])].append(row)

    compared = duplicate_pairs = 0
    for company_rows in groups.values():
        for index, left in enumerate(company_rows):
            left_title = normalise_text(left["title"])
            for right in company_rows[index + 1 :]:
                # Cheap blocking avoids description cosine work for unrelated titles.
                if token_set_ratio(left_title, normalise_text(right["title"])) < 75:
                    continue
                compared += 1
                decision = compare(left, right, vectors[int(left["id"])], vectors[int(right["id"])])
                if decision.duplicate:
                    duplicate_pairs += 1
                    uf.union(int(left["id"]), int(right["id"]))
                    decisions[(int(left["id"]), int(right["id"]))] = decision

    components: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        components[uf.find(int(row["id"]))].append(row)

    duplicates = 0
    if not dry_run:
        for row in rows:
            vector = vectors[int(row["id"])]
            conn.execute(
                """UPDATE postings
                   SET fingerprint = ?, embedding = ?,
                       status = CASE WHEN duplicate_of IS NOT NULL AND status = 'ignored' THEN 'new' ELSE status END,
                       duplicate_of = NULL, dedupe_signals = NULL
                   WHERE id = ?""",
                (fingerprint(row["title"], row["company_name"], row["description"]), embedding_to_blob(vector), row["id"]),
            )
        for component in components.values():
            if len(component) < 2:
                continue
            canonical = sorted(component, key=_canonical_rank)[0]
            canonical_id = int(canonical["id"])
            for row in component:
                row_id = int(row["id"])
                if row_id == canonical_id:
                    continue
                duplicates += 1
                pair = (min(canonical_id, row_id), max(canonical_id, row_id))
                decision = decisions.get(pair)
                detail = {
                    "signals": list(decision.signals) if decision else ["transitive_component"],
                    "fuzzy": round(decision.fuzzy_score, 1) if decision else None,
                    "cosine": round(decision.cosine, 4) if decision else None,
                }
                status_sql = "status = CASE WHEN status IN ('new','shortlisted') THEN 'ignored' ELSE status END, "
                conn.execute(
                    f"UPDATE postings SET duplicate_of = ?, dedupe_signals = ?, {status_sql}updated_at = datetime('now') WHERE id = ?",
                    (canonical_id, json.dumps(detail, sort_keys=True), row_id),
                )
        conn.commit()
    else:
        duplicates = sum(max(0, len(component) - 1) for component in components.values())

    return {
        "considered": len(rows),
        "pairs_compared": compared,
        "duplicate_pairs": duplicate_pairs,
        "duplicates": duplicates,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.dedupe", description=__doc__)
    parser.add_argument("--all", action="store_true", help="consider unmatched postings too")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)
    conn = db.connect()
    db.migrate(conn)
    result = dedupe(conn, matched_only=not args.all, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
