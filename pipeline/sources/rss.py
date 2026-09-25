"""Generic RSS 2.0 / Atom parser (research/D_job_sources.md sections 1-2): ICAEW
Training Vacancies, ACCA Careers, EY (successfactors-rss) and HSBC (avature-rss)
all reduce to "fetch a feed URL, read title/link/description/date per item".

Uses the standard library's xml.etree so no extra dependency is needed --
feedparser would be more forgiving of malformed XML, but every feed this
project targets validated as well-formed in manual testing, and the project's
brief is to avoid new dependencies unless one is actually needed.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from pipeline import http
from pipeline.sources.common import html_to_text, normalize_posting

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def fetch(entry: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = entry.get("endpoint") or {}
    url = endpoint.get("url")
    if not url:
        raise ValueError("rss entry missing endpoint.url")
    if not url.startswith("http"):
        url = f"https://{url}"

    resp = http.get(
        url,
        headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
    )
    resp.raise_for_status()
    return parse_feed(resp.content, entry, source_url=url)


def parse_feed(content: bytes, entry: dict[str, Any], source_url: str = "") -> list[dict[str, Any]]:
    """Pure normaliser (no network) so tests can feed it a captured fixture."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"rss feed at {source_url} did not parse as XML: {exc}") from exc

    if root.tag.endswith("feed"):
        return [_from_atom_entry(e, entry) for e in root.findall(f"{ATOM_NS}entry")]

    channel = root.find("channel")
    if channel is None:
        raise ValueError(
            f"rss feed at {source_url} has neither an <rss><channel> nor an Atom <feed> root (got <{root.tag}>)"
        )
    return [_from_rss_item(item, entry) for item in channel.findall("item")]


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None


def _from_rss_item(item: ET.Element, entry: dict[str, Any]) -> dict[str, Any]:
    company_name = entry.get("company") or "RSS"
    title = _text(item, "title") or ""
    link = _text(item, "link")
    raw_description = _text(item, "description")
    guid = _text(item, "guid") or link
    return normalize_posting(
        source="rss",
        company_name=company_name,
        external_id=guid,
        url=link,
        title=title,
        posted_at=_text(item, "pubDate"),
        description=html_to_text(raw_description),
        raw={"title": title, "link": link, "description": raw_description, "pubDate": _text(item, "pubDate"), "guid": guid},
    )


def _from_atom_entry(entry_el: ET.Element, entry: dict[str, Any]) -> dict[str, Any]:
    company_name = entry.get("company") or "RSS"

    def t(tag: str) -> str | None:
        child = entry_el.find(f"{ATOM_NS}{tag}")
        return child.text.strip() if child is not None and child.text else None

    link_el = entry_el.find(f"{ATOM_NS}link")
    link = link_el.get("href") if link_el is not None else None
    title = t("title") or ""
    summary = t("summary") or t("content")
    return normalize_posting(
        source="rss",
        company_name=company_name,
        external_id=t("id") or link,
        url=link,
        title=title,
        posted_at=t("published") or t("updated"),
        description=html_to_text(summary),
        raw={"title": title, "link": link, "summary": summary},
    )
