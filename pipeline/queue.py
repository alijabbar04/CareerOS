"""T-031: who should work on what, computed from TASKS.md.

    python -m pipeline.queue --driver claude-fable      # what this driver may take now, or who must run first
    python -m pipeline.queue --all                      # the matrix for every driver
    python -m pipeline.queue --board                    # matrix + each driver's status file + recent handoffs + what waits on the candidate

Drivers are `claude-fable`, `claude-opus`, `claude-sonnet`, `codex-sol`, `codex-luna`, `codex-terra`.
A task is offered to a driver when: its Status is `todo`, or `in-progress` claimed by that same driver;
every task in `Depends on` is `done`; the Models line's `Prefer` names this driver, its family
("Claude" or "Codex"), "either" or "both"; and `Skip if driver is` does not name it. Tasks whose
dependencies are owned or preferred by another driver are listed as blocked with that driver's name,
so `/careeros` can tell the candidate which session to start instead of guessing.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from pipeline import config

TASKS_PATH = config.REPO_ROOT / "TASKS.md"
DRIVERS = ["claude-fable", "claude-opus", "claude-sonnet", "codex-sol", "codex-luna", "codex-terra"]
CARD_RE = re.compile(r"^### (T-\d{3}) ([^\n]*)\n(.*?)(?=^### |^## |\Z)", re.M | re.S)
FIELD_RE = re.compile(r"^\s*- (Status|Models|Favoured|Depends on): (.*)$", re.M)


def parse_tasks(text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for m in CARD_RE.finditer(text):
        tid, title, body = m.group(1), m.group(2).strip(), m.group(3)
        fields = {k: v.strip() for k, v in FIELD_RE.findall(body)}
        status = fields.get("Status", "todo")
        state = _state(status)
        models = fields.get("Models") or fields.get("Favoured") or ""
        prefer = _between(models, "Prefer:", "· Skip if driver is:") or ""
        skip = _after(models, "Skip if driver is:") or ""
        depends = [d for d in re.findall(r"T-\d{3}", fields.get("Depends on", ""))]
        tasks.append({
            "id": tid, "title": title, "status": status, "state": state, "owner": _owner(status),
            "models": models, "prefer": prefer.strip(), "skip": skip.strip(), "depends": depends,
        })
    return tasks


def _between(text: str, start: str, end: str) -> str | None:
    if start not in text:
        return None
    rest = text.split(start, 1)[1]
    return rest.split(end, 1)[0] if end in rest else rest


def _after(text: str, marker: str) -> str | None:
    return text.split(marker, 1)[1] if marker in text else None


def _state(status: str) -> str:
    s = status.casefold()
    if s.startswith("done"):
        return "done"
    if s.startswith("in-progress") or s.startswith("in progress"):
        return "in-progress"
    if s.startswith("blocked"):
        return "blocked"
    return "todo"


def _owner(status: str) -> str | None:
    """Driver id from `in-progress (Claude Fable, 2026-09-23)`; bare `Claude` or `Codex` gives the family only."""
    m = re.match(r"\s*in[- ]progress\s*\(([^,)]+)", status, re.I)
    if not m:
        return None
    words = m.group(1).casefold().split()
    if not words or words[0] not in {"claude", "codex"}:
        return None
    if len(words) >= 2 and words[1] in {"fable", "opus", "sonnet", "haiku", "sol", "luna", "terra", "astra"}:
        return f"{words[0]}-{words[1]}"
    return words[0]


def _family(driver: str) -> str:
    return driver.split("-", 1)[0]


def prefers(task: dict[str, Any], driver: str) -> bool:
    p = task["prefer"].casefold()
    if not p or "either" in p or "both" in p or "any" in p:
        return True
    family, model = driver.split("-", 1)
    other = "codex" if family == "claude" else "claude"
    mentions_me = family in p
    mentions_other = other in p
    if mentions_me and mentions_other:
        return True  # "built by Codex, reviewed by Claude": both have a part
    if not mentions_me:
        return False
    named = re.findall(rf"{family}\s+(fable|opus|sonnet|haiku|sol|luna|terra|astra)", p)
    return (model in named) if named else True


def skipped(task: dict[str, Any], driver: str) -> bool:
    s = task["skip"].casefold()
    return _family(driver) in s


def owned_by_other(task: dict[str, Any], driver: str) -> bool:
    owner = task["owner"]
    if not owner:
        return False
    if owner == driver:
        return False
    if "-" not in owner:  # bare family: another session of the same family, or the other family
        return True
    return True


def evaluate(tasks: list[dict[str, Any]], driver: str) -> dict[str, Any]:
    by_id = {t["id"]: t for t in tasks}
    go: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for t in tasks:
        if t["state"] == "done" or t["state"] == "blocked":
            continue
        if skipped(t, driver) or not prefers(t, driver):
            continue
        if t["state"] == "in-progress" and owned_by_other(t, driver):
            continue
        reasons: list[str] = []
        for dep in t["depends"]:
            d = by_id.get(dep)
            if d is None:
                reasons.append(f"{dep} is not in TASKS.md")
            elif d["state"] != "done":
                who = d["owner"] or _preferred_driver(d) or "either driver"
                reasons.append(f"{dep} ({d['state']}) must finish first: {who}")
        (blocked if reasons else go).append({**t, "reasons": reasons})
    return {"driver": driver, "go": go, "blocked": blocked}


def _preferred_driver(task: dict[str, Any]) -> str | None:
    p = task["prefer"].casefold()
    for d in DRIVERS:
        family, model = d.split("-", 1)
        if f"{family} {model}" in p:
            return d
    if "claude" in p and "codex" not in p:
        return "claude"
    if "codex" in p and "claude" not in p:
        return "codex"
    return None


def verdict(tasks: list[dict[str, Any]], driver: str) -> str:
    result = evaluate(tasks, driver)
    lines = [f"Driver: {driver}"]
    if result["go"]:
        first = result["go"][0]
        lines.append(f"GO: {first['id']} {first['title']}" + (f"  [continuing: {first['status'][:60]}]" if first["state"] == "in-progress" else ""))
        for t in result["go"][1:]:
            lines.append(f"  then {t['id']} {t['title']}")
    else:
        lines.append("WAIT: nothing unblocked for this driver.")
    for t in result["blocked"]:
        lines.append(f"  blocked {t['id']} {t['title']}: " + "; ".join(t["reasons"]))
    others = []
    for d in DRIVERS:
        if d == driver or _family(d) == _family(driver) and d.split("-")[1] in {"sonnet", "luna", "terra"}:
            continue
        r = evaluate(tasks, d)
        if r["go"]:
            others.append(f"{d}: {r['go'][0]['id']} {r['go'][0]['title']}")
    if others:
        lines.append(("Start another session for: " if not result["go"] else "Other drivers could run: ") + " | ".join(others))
    return "\n".join(lines)


def matrix(tasks: list[dict[str, Any]]) -> str:
    lines = []
    for d in ("claude-fable", "claude-opus", "codex-sol"):
        r = evaluate(tasks, d)
        lines.append(f"## {d}")
        lines += [f"- GO {t['id']} {t['title']}" + (" (continuing)" if t["state"] == "in-progress" else "") for t in r["go"]] or ["- nothing unblocked"]
        lines += [f"- blocked {t['id']} {t['title']}: " + "; ".join(t["reasons"]) for t in r["blocked"]]
        lines.append("")
    return "\n".join(lines)


STATUS_DIR = config.REPO_ROOT / ".claude-mem" / "status"
HANDOFFS_PATH = config.REPO_ROOT / ".claude-mem" / "handoffs.md"


def waiting_on_ali(tasks: list[dict[str, Any]]) -> list[str]:
    out = []
    for t in tasks:
        if t["state"] == "blocked" and "ali" in t["status"].casefold():
            detail = t["status"].split(":", 1)[-1].strip().rstrip(")") if ":" in t["status"] else t["status"]
            out.append(f"{t['id']} {t['title']}: {detail}")
    return out


def _section(text: str, header_prefix: str) -> str:
    out: list[str] = []
    capture = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            capture = ln.startswith(header_prefix)
            continue
        if capture and ln.strip().startswith("- "):
            out.append(ln.strip()[2:])
    return "; ".join(out)


def board(tasks: list[dict[str, Any]]) -> str:
    """Everything a driver or the candidate needs at once: queues, each driver's own status file, recent handoffs, the candidate's items."""
    lines = [matrix(tasks), "## Driver status files (each driver rewrites only its own)"]
    for d in ("claude-fable", "claude-opus", "codex-sol"):
        path = STATUS_DIR / f"{d}.md"
        if not path.exists():
            lines.append(f"- {d}: no status file yet")
            continue
        text = path.read_text(encoding="utf-8")
        updated = next((ln.replace("Updated:", "").strip() for ln in text.splitlines() if ln.startswith("Updated:")), "?")
        now = _section(text, "## Now")
        blocked = _section(text, "## Blocked")
        lines.append(f"- {d} (updated {updated}): now: {now or 'nothing recorded'}" + (f"; blocked: {blocked}" if blocked else ""))
    lines.append("")
    if HANDOFFS_PATH.exists():
        entries = [ln for ln in HANDOFFS_PATH.read_text(encoding="utf-8").splitlines() if ln[:4].isdigit()]
        lines.append("## Recent handoffs")
        lines += [f"- {ln}" for ln in entries[-8:]] or ["- none"]
        lines.append("")
    ali = waiting_on_ali(tasks)
    lines.append("## Waiting on the candidate (tasks marked blocked on him)")
    lines += [f"- {a}" for a in ali] or ["- nothing"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.queue", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--driver", choices=DRIVERS)
    parser.add_argument("--all", action="store_true", help="queues for every driver")
    parser.add_argument("--board", action="store_true", help="queues plus driver status files, handoffs and what waits on the candidate")
    parser.add_argument("--tasks", type=Path, default=TASKS_PATH)
    args = parser.parse_args(argv)
    tasks = parse_tasks(args.tasks.read_text(encoding="utf-8"))
    if args.board:
        print(board(tasks))
    elif args.all or not args.driver:
        print(matrix(tasks))
    else:
        print(verdict(tasks, args.driver))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
