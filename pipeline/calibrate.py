"""T-030: tabulate a model-calibration run.

    python -m pipeline.calibrate brain/vault/applications/0055-dixon-wilson --arms fable opus

Each arm lives in `<application>/calibration/<arm>/` with the same `brief.md`, its own
`drafts/` and `reports/`. Per draft the table shows: words, deterministic hard failures
and warnings (from `reports/verify-<stem>.json`), the verifier verdict, the style score,
the red-team verdict and rubric total (from the agent reports' first lines), and the
sentence-level metrics the verifier computed. An optional `usage.json` per arm
(`{"tokens": N, "rounds": {"answer-1": 2}, "notes": "..."}`) is folded into the totals.
Nothing here judges quality itself; it lines the two arms up so the decision in
DECISIONS.md can cite numbers.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)", re.I)
STYLE_RE = re.compile(r"STYLE:\s*(PASS|FAIL)", re.I)
REDTEAM_RE = re.compile(r"RED TEAM:\s*(PASS|FAIL)", re.I)
RUBRIC_RE = re.compile(r"(\d{1,2})\s*/\s*25")


def _first_match(path: Path, pattern: re.Pattern[str]) -> str | None:
    if not path.exists():
        return None
    m = pattern.search(path.read_text(encoding="utf-8"))
    return m.group(1).upper() if m else None


def _rubric(path: Path) -> int | None:
    if not path.exists():
        return None
    m = RUBRIC_RE.search(path.read_text(encoding="utf-8"))
    return int(m.group(1)) if m else None


def arm_rows(arm_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for draft in sorted((arm_dir / "drafts").glob("*.md")):
        stem = draft.stem
        reports = arm_dir / "reports"
        verify_json = reports / f"verify-{stem}.json"
        data = json.loads(verify_json.read_text(encoding="utf-8")) if verify_json.exists() else {}
        metrics = data.get("metrics", {})
        rows.append(
            {
                "draft": stem,
                "words": metrics.get("words"),
                "hard": len(data.get("hard_fails", [])) if data else None,
                "warnings": len(data.get("warnings", [])) if data else None,
                "claims_per_100": metrics.get("claims_per_100_words"),
                "research_sentences": metrics.get("research_sentences"),
                "sd_ratio": metrics.get("sentence_len_sd_ratio"),
                "verifier": _first_match(reports / f"verifier-{stem}.md", VERDICT_RE),
                "style": _first_match(reports / f"critic-{stem}.md", STYLE_RE),
                "redteam": _first_match(reports / f"redteam-{stem}.md", REDTEAM_RE),
                "rubric": _rubric(reports / f"redteam-{stem}.md"),
                "fact_requests": len(data.get("review", {}).get("fact_requests", [])) if data else None,
            }
        )
    return rows


def summarise(arm_dir: Path) -> dict[str, Any]:
    rows = arm_rows(arm_dir)
    usage_path = arm_dir / "usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
    rubrics = [r["rubric"] for r in rows if r["rubric"] is not None]
    return {
        "arm": arm_dir.name,
        "drafts": len(rows),
        "hard_total": sum(r["hard"] or 0 for r in rows),
        "warnings_total": sum(r["warnings"] or 0 for r in rows),
        "verifier_pass": sum(1 for r in rows if r["verifier"] == "PASS"),
        "style_pass": sum(1 for r in rows if r["style"] == "PASS"),
        "redteam_pass": sum(1 for r in rows if r["redteam"] == "PASS"),
        "rubric_mean": round(sum(rubrics) / len(rubrics), 1) if rubrics else None,
        "claims_per_100_mean": round(sum(r["claims_per_100"] or 0 for r in rows) / max(1, len(rows)), 2),
        "fact_requests": sum(r["fact_requests"] or 0 for r in rows),
        "tokens": usage.get("tokens"),
        "rounds": usage.get("rounds"),
        "notes": usage.get("notes"),
        "rows": rows,
    }


def markdown(application_dir: Path, arms: list[str]) -> str:
    summaries = [summarise(application_dir / "calibration" / arm) for arm in arms]
    lines = [f"# Calibration: {application_dir.name}", ""]
    lines += ["| arm | drafts | hard fails | warnings | verifier PASS | style PASS | red team PASS | rubric mean /25 | claims per 100 words | fact requests | tokens | rounds |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s['arm']} | {s['drafts']} | {s['hard_total']} | {s['warnings_total']} | {s['verifier_pass']}/{s['drafts']} | {s['style_pass']}/{s['drafts']} | "
            f"{s['redteam_pass']}/{s['drafts']} | {s['rubric_mean'] if s['rubric_mean'] is not None else 'n/a'} | {s['claims_per_100_mean']} | {s['fact_requests']} | "
            f"{s['tokens'] if s['tokens'] is not None else 'n/a'} | {json.dumps(s['rounds']) if s['rounds'] else 'n/a'} |"
        )
    lines.append("")
    for s in summaries:
        lines += [f"## {s['arm']}", "", "| draft | words | hard | warn | claims/100 | research | sd/mean | verifier | style | red team | rubric |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in s["rows"]:
            lines.append(f"| {r['draft']} | {r['words']} | {r['hard']} | {r['warnings']} | {r['claims_per_100']} | {r['research_sentences']} | {r['sd_ratio']} | {r['verifier'] or '-'} | {r['style'] or '-'} | {r['redteam'] or '-'} | {r['rubric'] if r['rubric'] is not None else '-'} |")
        if s["notes"]:
            lines += ["", f"Notes: {s['notes']}"]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.calibrate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("application", type=Path, help="application folder containing calibration/<arm>/")
    parser.add_argument("--arms", nargs="+", default=["fable", "opus"])
    parser.add_argument("--output", type=Path, help="write the Markdown table here as well as printing it")
    args = parser.parse_args(argv)
    text = markdown(args.application, args.arms)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
