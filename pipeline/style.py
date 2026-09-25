"""T-005: measure the candidate's writing fingerprint and score drafts against it.

    python -m pipeline.style fingerprint          # measure the voice corpus -> brain/vault/voice/fingerprint.json
    python -m pipeline.style check <file> [--kind cover-letter|answer|email]   # score a draft
    python -m pipeline.style corpus               # list the files in the voice corpus

Only sources with `voice_corpus: true` in their front matter are measured. The fingerprint is
descriptive (what the candidate's writing measurably does); the phrase lists in brain/vault/voice/phrases.yaml
are prescriptive (what a draft must avoid). Both feed the style critic in T-017.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

import yaml

from pipeline import config

VOICE_DIR = config.VAULT_DIR / "voice"
FINGERPRINT_PATH = VOICE_DIR / "fingerprint.json"
PHRASES_PATH = VOICE_DIR / "phrases.yaml"

KIND_GROUPS = {"cover-letter": "cover-letter", "speculative-email": "email", "application-answers": "answer"}

CONTRACTION_RE = re.compile(r"\b(i'm|i've|i'd|i'll|it's|that's|there's|don't|doesn't|isn't|wasn't|can't|won't|didn't|you're|they're|we're|wouldn't|couldn't|shouldn't)\b", re.I)
UK_RE = re.compile(r"\b\w+(ise|ised|ising|isation|isations|our|ours|yse|ysed|ysing)\b")
US_RE = re.compile(r"\b\w+(ize|ized|izing|ization|izations|yze|yzed|yzing)\b|\b(color|honor|favor|labor|behavior|center|organization)s?\b")
QUESTION_PATTERNS = re.compile(r"^(please |why |what |describe |how |tell |give |imagine |outside |this role|question \d|q\d|\d+\.)", re.I)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def read_source(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        return yaml.safe_load(fm) or {}, body
    return {}, raw


def voice_corpus() -> list[tuple[dict, str]]:
    out = []
    for p in sorted(config.SOURCES_DIR.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        meta, body = read_source(p)
        if meta.get("voice_corpus"):
            out.append((meta, body))
    return out


def strip_question_lines(text: str) -> str:
    """Application-answer documents contain the questions; drop lines that look like questions."""
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.endswith("?") and len(s.split()) < 40:
            continue
        if QUESTION_PATTERNS.match(s) and len(s.split()) < 30:
            continue
        kept.append(s)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text)
    return [s.strip() for s in parts if len(s.split()) >= 3]


def measure(text: str) -> dict:
    text = text.replace("’", "'").replace("‘", "'")  # curly apostrophes from Word and PDF exports hid every I'm from the contraction count
    sents = sentences(text)
    lens = [len(s.split()) for s in sents] or [0]
    words = re.findall(r"[A-Za-z']+", text.lower())
    n = max(1, len(words))
    paras = [p for p in text.split("\n") if len(p.split()) > 15]
    plens = [len(p.split()) for p in paras] or [0]
    openers = Counter(" ".join(s.split()[:3]).lower() for s in sents)
    return {
        "words": len(words),
        "sentences": len(sents),
        "sentence_len_mean": round(statistics.mean(lens), 1),
        "sentence_len_median": statistics.median(lens),
        "sentence_len_sd": round(statistics.pstdev(lens), 1),
        "sentence_len_sd_ratio": round(statistics.pstdev(lens) / statistics.mean(lens), 2) if statistics.mean(lens) else 0,
        "sentence_len_min": min(lens),
        "sentence_len_max": max(lens),
        "paragraphs": len(paras),
        "paragraph_len_mean": round(statistics.mean(plens), 0),
        "em_dashes": text.count("—"),
        "en_dashes": text.count("–"),
        "semicolons": text.count(";"),
        "exclamations": text.count("!"),
        "question_marks": text.count("?"),
        "contractions_per_1k": round(len(CONTRACTION_RE.findall(text)) / n * 1000, 1),
        "first_person_per_1k": round(len(re.findall(r"\bI\b", text)) / n * 1000, 1),
        "uk_spellings": len(UK_RE.findall(text)),
        "us_spellings": len(US_RE.findall(text)),
        "top_openers": openers.most_common(10),
    }


def phrase_hits(text: str, lists: dict) -> dict:
    low = text.lower()
    words = max(1, len(re.findall(r"[A-Za-z']+", low)))
    def hits(items):
        found = {}
        for item in items or []:
            item_l = str(item).lower()
            pattern = r"\b" + re.escape(item_l) + r"\b"  # word boundaries for every phrase: "in conclusion" must not match "explain conclusions"
            c = len(re.findall(pattern, low))
            if c:
                found[item_l] = c
        return found
    banned = hits(lists.get("banned")) | hits(lists.get("personal_banned"))
    discouraged = hits(lists.get("discouraged"))
    return {
        "banned_hits": banned,
        "discouraged_hits": discouraged,
        "discouraged_per_300_words": round(sum(discouraged.values()) / words * 300, 2),
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_fingerprint() -> None:
    lists = yaml.safe_load(PHRASES_PATH.read_text(encoding="utf-8")) if PHRASES_PATH.exists() else {}
    groups: dict[str, list[str]] = {}
    for meta, body in voice_corpus():
        g = KIND_GROUPS.get(meta.get("kind"), "other")
        text = strip_question_lines(body) if g == "answer" else body
        groups.setdefault(g, []).append(text)
    fp = {"measured_on": str(config.SOURCES_DIR), "groups": {}}
    for g, texts in groups.items():
        joined = "\n".join(texts)
        m = measure(joined)
        m["documents"] = len(texts)
        m.update(phrase_hits(joined, lists))
        fp["groups"][g] = m
        print(f"{g:14s} docs={len(texts):3d} words={m['words']:6d} sent_mean={m['sentence_len_mean']:5.1f} sd={m['sentence_len_sd']:4.1f} "
              f"em_dashes={m['em_dashes']} contractions/1k={m['contractions_per_1k']} uk/us={m['uk_spellings']}/{m['us_spellings']} "
              f"banned={sum(m['banned_hits'].values())} discouraged={sum(m['discouraged_hits'].values())}")
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    FINGERPRINT_PATH.write_text(json.dumps(fp, indent=2), encoding="utf-8")
    print(f"-> {FINGERPRINT_PATH}")


META_HEADINGS = ("do not say", "what must never appear", "notes", "citations")


def strip_meta_sections(text: str) -> str:
    """Drop front matter and sections that deliberately quote banned phrases (e.g. '## Do not say')."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) > 2 else text
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            skipping = any(h in line.lower() for h in META_HEADINGS)
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


def cmd_check(path: Path, kind: str) -> int:
    text = strip_meta_sections(path.read_text(encoding="utf-8"))
    lists = yaml.safe_load(PHRASES_PATH.read_text(encoding="utf-8")) if PHRASES_PATH.exists() else {}
    fp = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8")) if FINGERPRINT_PATH.exists() else {"groups": {}}
    ref = fp["groups"].get(kind) or next(iter(fp["groups"].values()), {})
    m = measure(text)
    m.update(phrase_hits(text, lists))
    flags = []
    if m["banned_hits"]:
        flags.append(f"FAIL banned phrases: {m['banned_hits']}")
    if m["discouraged_per_300_words"] > 3:
        flags.append(f"WARN discouraged density {m['discouraged_per_300_words']} per 300 words: {m['discouraged_hits']}")
    if m["em_dashes"] > 0:
        flags.append(f"WARN {m['em_dashes']} em-dash(es); the candidate uses none")
    if m["exclamations"] > 0:
        flags.append("WARN exclamation mark; the candidate uses none")
    if m["us_spellings"] > 0:
        flags.append(f"WARN {m['us_spellings']} US spelling(s)")
    if ref:
        if abs(m["sentence_len_mean"] - ref["sentence_len_mean"]) > 5:
            flags.append(f"WARN mean sentence length {m['sentence_len_mean']} vs the candidate's {ref['sentence_len_mean']}")
        if m["sentence_len_sd_ratio"] < 0.3:
            flags.append(f"WARN low sentence-length variation (sd/mean {m['sentence_len_sd_ratio']}; the candidate's {ref['sentence_len_sd_ratio']})")
        if kind == "cover-letter" and m["contractions_per_1k"] > 3:
            flags.append(f"WARN contractions {m['contractions_per_1k']}/1k in a letter; the candidate's letters have {ref['contractions_per_1k']}")
    print(json.dumps({k: v for k, v in m.items() if k != "top_openers"}, indent=2))
    for f in flags:
        print(f)
    return 1 if any(f.startswith("FAIL") for f in flags) else 0


def cmd_corpus() -> None:
    for meta, body in voice_corpus():
        print(f"{meta.get('kind'):20s} {meta.get('id')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fingerprint")
    sub.add_parser("corpus")
    c = sub.add_parser("check")
    c.add_argument("file", type=Path)
    c.add_argument("--kind", default="cover-letter", choices=["cover-letter", "answer", "email"])
    args = ap.parse_args()
    if args.cmd == "fingerprint":
        cmd_fingerprint()
    elif args.cmd == "corpus":
        cmd_corpus()
    else:
        sys.exit(cmd_check(args.file, args.kind))
