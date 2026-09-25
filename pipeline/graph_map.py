"""Build the CareerOS Graphify map without sending personal data off-device.

The normal Graphify semantic extractor expects either a hosted LLM or agent
workers.  CareerOS deliberately has neither available in every session, so this
module creates the same Graphify-compatible graph deterministically from the
explicit structure already present in ``brain/vault`` and ``brain/sources``.

The graph is a navigation aid.  It does not promote inferred connections to
facts: applications must still resolve every statement through the claims
ledger and its source document.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
from graphify.cluster import cluster, score_all
from graphify.export import to_json


SUPPORTED_SUFFIXES = {".md", ".json", ".yaml", ".yml"}
WORD_RE = re.compile(r"[a-z][a-z0-9'-]{2,}", re.IGNORECASE)
CLAIM_RE = re.compile(r"\bC-\d{4}\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:£|\$)?\d[\d,]*(?:\.\d+)?%?(?:/\d+(?:\.\d+)?)?")
HEADING_RE = re.compile(r"(?m)^(#{1,3})\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
CLAIM_CITATION_RE = re.compile(r"\[(?:C-\d{4}(?:,\s*)?)+\]")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
STOPWORDS = {
    "about", "after", "also", "been", "being", "between", "could", "from",
    "have", "into", "more", "most", "that", "their", "there", "these", "they",
    "this", "those", "through", "using", "very", "were", "what", "when", "where",
    "which", "while", "with", "would", "your", "application", "cover", "letter",
    "role", "company", "team", "work", "working",
}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "item"


def _title_case(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").strip().capitalize()


def _frontmatter(text: str) -> dict[str, str | list[str]]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    result: dict[str, str | list[str]] = {}
    for raw_line in match.group(1).splitlines():
        if ":" not in raw_line:
            continue
        key, raw_value = raw_line.split(":", 1)
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            result[key.strip()] = [
                item.strip().strip("'\"")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        else:
            result[key.strip()] = value.strip("'\"")
    return result


def _document_title(path: Path, text: str) -> str:
    for _, heading in HEADING_RE.findall(text):
        return re.sub(r"\s+", " ", heading).strip()
    return _title_case(path.stem)


def _clean_sentence(value: str) -> str:
    value = CLAIM_CITATION_RE.sub("", value)
    value = MARKDOWN_LINK_RE.sub(r"\1", value)
    value = re.sub(r"[`*_>#|]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -:;")
    return value


def _number_sentences(relative_path: str, text: str) -> list[str]:
    body = FRONTMATTER_RE.sub("", text, count=1)
    if "/stories/" in f"/{relative_path.replace(chr(92), '/')}":
        result = re.search(r"(?ms)^## Result\s*(.*?)(?=^##\s|\Z)", body)
        if result:
            body = result.group(1)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", body)
    found: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        cleaned = _clean_sentence(sentence)
        without_claim_ids = CLAIM_RE.sub("", cleaned)
        if not NUMBER_RE.search(without_claim_ids):
            continue
        if len(cleaned) < 12:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(cleaned[:2000].rstrip())
        if len(found) == 10:
            break
    return found


def _safe_numbers(sentence: str) -> list[str]:
    """Return compact numeric evidence while excluding phone-like identifiers."""
    values: list[str] = []
    for match in NUMBER_RE.finditer(CLAIM_RE.sub("", sentence)):
        value = match.group(0)
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 8:
            continue
        if value not in values:
            values.append(value)
        if len(values) == 8:
            break
    return values


def _tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in WORD_RE.findall(CLAIM_RE.sub("", text))
        if token.casefold() not in STOPWORDS
    }


def _add_edge(
    graph: nx.Graph,
    source: str,
    target: str,
    relation: str,
    *,
    confidence: str,
    source_file: str,
    confidence_score: float,
    source_location: str | None = None,
) -> None:
    if source == target:
        return
    graph.add_edge(
        source,
        target,
        relation=relation,
        confidence=confidence,
        confidence_score=confidence_score,
        source_file=source_file,
        source_location=source_location,
        weight=1.0,
        context="documentation",
        _src=source,
        _tgt=target,
    )


def _add_concept(graph: nx.Graph, label: str) -> str:
    node_id = f"concept_{_slug(label)}"
    if node_id not in graph:
        graph.add_node(
            node_id,
            label=label,
            file_type="concept",
            source_file="brain",
            source_location=None,
        )
    return node_id


def build_graph(project_root: Path, output_dir: Path) -> dict[str, object]:
    """Build and write a deterministic Graphify-compatible graph."""
    project_root = project_root.resolve()
    brain_root = project_root / "brain"
    roots = (brain_root / "vault", brain_root / "sources")
    files = sorted(
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        raise RuntimeError("No supported files found under brain/vault or brain/sources")

    graph = nx.Graph()
    vault_id = _add_concept(graph, "Curated vault")
    sources_id = _add_concept(graph, "Source corpus")
    number_hub = _add_concept(graph, "Quantified result (number)")
    document_tokens: dict[str, set[str]] = {}
    document_paths: dict[str, str] = {}
    total_words = 0

    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(project_root).as_posix()
        total_words += len(re.findall(r"\b\w+\b", text))
        metadata = _frontmatter(text)
        doc_id = f"{_slug(str(path.relative_to(project_root).with_suffix('')))}_document"
        title = _document_title(path, text)
        graph.add_node(
            doc_id,
            label=title,
            file_type="document",
            source_file=relative,
            source_location="L1",
            document_kind=str(metadata.get("kind", "source")),
        )
        parent_id = vault_id if relative.startswith("brain/vault/") else sources_id
        _add_edge(
            graph,
            parent_id,
            doc_id,
            "contains",
            confidence="EXTRACTED",
            confidence_score=1.0,
            source_file=relative,
            source_location="L1",
        )
        document_tokens[doc_id] = _tokens(f"{title}\n{text}")
        document_paths[doc_id] = relative

        for heading_index, (marks, heading) in enumerate(HEADING_RE.findall(text)):
            if len(marks) == 1:
                continue
            heading = _clean_sentence(heading)
            if not heading:
                continue
            section_id = f"{doc_id}_section_{heading_index}_{_slug(heading)[:48]}"
            graph.add_node(
                section_id,
                label=f"Section: {heading}",
                file_type="document",
                source_file=relative,
                source_location=heading,
            )
            _add_edge(
                graph,
                doc_id,
                section_id,
                "contains_section",
                confidence="EXTRACTED",
                confidence_score=1.0,
                source_file=relative,
                source_location=heading,
            )

        for field, relation in (("competencies", "demonstrates"), ("tracks", "supports_track")):
            values = metadata.get(field, [])
            if not isinstance(values, list):
                continue
            for value in values:
                label = _title_case(value)
                concept_id = _add_concept(graph, label)
                _add_edge(
                    graph,
                    doc_id,
                    concept_id,
                    relation,
                    confidence="EXTRACTED",
                    confidence_score=1.0,
                    source_file=relative,
                    source_location=f"frontmatter:{field}",
                )

        for claim_id in sorted(set(CLAIM_RE.findall(text))):
            node_id = f"claim_{claim_id.lower().replace('-', '_')}"
            if node_id not in graph:
                graph.add_node(
                    node_id,
                    label=f"Claim {claim_id}",
                    file_type="concept",
                    source_file="brain/claims.jsonl",
                    source_location=claim_id,
                )
            _add_edge(
                graph,
                doc_id,
                node_id,
                "references_claim",
                confidence="EXTRACTED",
                confidence_score=1.0,
                source_file=relative,
            )

        for result_index, sentence in enumerate(_number_sentences(relative, text)):
            values = _safe_numbers(sentence)
            if not values:
                continue
            result_id = f"{doc_id}_quantified_{result_index}"
            value_summary = ", ".join(values)
            graph.add_node(
                result_id,
                label=f"Quantified evidence: {value_summary} — {title}",
                file_type="concept",
                source_file=relative,
                source_location="numeric statement",
            )
            _add_edge(
                graph,
                doc_id,
                result_id,
                "states_quantified_evidence",
                confidence="EXTRACTED",
                confidence_score=1.0,
                source_file=relative,
                source_location="numeric statement",
            )
            # Keep the query-oriented number hub precise: it answers which
            # curated *stories* carry quantified results without exploding
            # traversal through addresses, dates and form fields in sources.
            if relative.startswith("brain/vault/stories/"):
                _add_edge(
                    graph,
                    result_id,
                    number_hub,
                    "is_quantified_result",
                    confidence="EXTRACTED",
                    confidence_score=1.0,
                    source_file=relative,
                    source_location="numeric statement",
                )

    # One conservative similarity link per document. These are deliberately
    # labelled INFERRED and exist only to aid navigation across related files.
    doc_ids = sorted(document_tokens)
    inferred_edges = 0
    for index, left in enumerate(doc_ids):
        left_tokens = document_tokens[left]
        best: tuple[float, str] | None = None
        for right in doc_ids[index + 1 :]:
            right_tokens = document_tokens[right]
            shared = left_tokens & right_tokens
            if len(shared) < 8:
                continue
            union = left_tokens | right_tokens
            score = len(shared) / len(union) if union else 0.0
            if score >= 0.20 and (best is None or score > best[0]):
                best = (score, right)
        if best is None:
            continue
        score, right = best
        _add_edge(
            graph,
            left,
            right,
            "semantically_similar_to",
            confidence="INFERRED",
            confidence_score=0.65 if score < 0.35 else 0.75,
            source_file=f"{document_paths[left]}; {document_paths[right]}",
        )
        inferred_edges += 1

    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    labels: dict[int, str] = {}
    for community_id, members in communities.items():
        def label_priority(node_id: str) -> tuple[int, int, str]:
            data = graph.nodes[node_id]
            label = str(data.get("label", node_id))
            if data.get("source_file") == "brain" and data.get("file_type") == "concept":
                priority = 0
            elif label.startswith("Claim C-"):
                priority = 1
            elif data.get("file_type") == "document" and data.get("source_location") == "L1":
                priority = 2
            elif label.startswith("Quantified evidence:"):
                priority = 4
            else:
                priority = 3
            return priority, -graph.degree(node_id), label

        ranked = sorted(
            members,
            key=label_priority,
        )
        labels[community_id] = str(graph.nodes[ranked[0]].get("label", ranked[0]))[:80]

    output_dir.mkdir(parents=True, exist_ok=True)
    graph_path = output_dir / "graph.json"
    to_json(
        graph,
        communities,
        str(graph_path),
        force=True,
        community_labels=labels,
    )
    (output_dir / ".graphify_labels.json").write_text(
        json.dumps({str(key): value for key, value in labels.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / ".graphify_python").write_text(sys.executable, encoding="utf-8")
    (output_dir / ".graphify_root").write_text(str(brain_root.resolve()), encoding="utf-8")

    degrees = sorted(graph.degree, key=lambda item: (-item[1], str(item[0])))
    confidence_counts = Counter(data.get("confidence", "EXTRACTED") for _, _, data in graph.edges(data=True))
    report_lines = [
        "# CareerOS Graph Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Scope and trust boundary",
        "",
        f"This map covers {len(files)} files under `brain/vault` and `brain/sources` (~{total_words:,} words).",
        "Graph edges are inferred and not a source of truth. `EXTRACTED` edges come from explicit file structure, frontmatter, claim IDs or numeric text; `INFERRED` similarity edges are navigational only. Resolve every application fact through `brain/claims.jsonl` and the cited source before use.",
        "",
        "## Summary",
        "",
        f"- Nodes: {graph.number_of_nodes():,}",
        f"- Edges: {graph.number_of_edges():,}",
        f"- Communities: {len(communities):,}",
        f"- Confidence: EXTRACTED {confidence_counts['EXTRACTED']:,}; INFERRED {confidence_counts['INFERRED']:,}; AMBIGUOUS {confidence_counts['AMBIGUOUS']:,}",
        "- Semantic extraction token cost: 0 input / 0 output (deterministic local build)",
        "",
        "## Community cohesion",
        "",
    ]
    for community_id in sorted(communities):
        report_lines.append(
            f"- {community_id}: {labels[community_id]} — cohesion {cohesion.get(community_id, 0.0):.6f}"
        )
    report_lines.extend(["", "## God Nodes", ""])
    for node_id, degree in degrees[:10]:
        report_lines.append(f"- {graph.nodes[node_id].get('label', node_id)} — degree {degree}")
    report_lines.extend(["", "## Surprising Connections", ""])
    inferred = [
        (graph.nodes[left].get("label", left), graph.nodes[right].get("label", right), data)
        for left, right, data in graph.edges(data=True)
        if data.get("confidence") == "INFERRED"
    ]
    if inferred:
        for left, right, data in inferred[:10]:
            report_lines.append(
                f"- {left} ↔ {right} — `{data.get('relation')}` (INFERRED, {data.get('confidence_score')})"
            )
    else:
        report_lines.append("- None.")
    report_lines.extend(
        [
            "",
            "## Suggested Questions",
            "",
            '- "Which stories show attention to detail with a number?"',
            '- "Which stories demonstrate leadership and include quantified evidence?"',
            '- "Which source documents support the most claims?"',
            '- "Where do the curated stories and original source corpus connect?"',
            "",
        ]
    )
    (output_dir / "GRAPH_REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")
    metadata = {
        "build_mode": "deterministic-explicit-structure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": len(files),
        "words": total_words,
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "communities": len(communities),
        "inferred_edges": inferred_edges,
        "semantic_input_tokens": 0,
        "semantic_output_tokens": 0,
    }
    (output_dir / "build-metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("graphify-out"))
    args = parser.parse_args()
    metadata = build_graph(args.project_root, args.output)
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
