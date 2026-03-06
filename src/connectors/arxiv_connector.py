"""arXiv feed parser and connector helpers."""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime


ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_HEADERS = {
    "User-Agent": "daily-paper-push-mvp/0.1 (+https://github.com)",
    "Accept": "application/atom+xml",
}


def fetch_arxiv_by_date(
    *,
    target_date: date,
    categories: list[str],
    focus_terms: list[str] | None = None,
    focus_mode: str = "any",
    max_results: int = 50,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """Fetch arXiv papers by date and categories via official API."""
    start = target_date.strftime("%Y%m%d0000")
    end = target_date.strftime("%Y%m%d2359")

    date_query = f"submittedDate:[{start} TO {end}]"
    search_query = _build_search_query(
        date_query=date_query,
        categories=categories,
        focus_terms=focus_terms or [],
        focus_mode=focus_mode,
    )

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max(1, int(max_results)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=DEFAULT_HEADERS)

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        xml_text = response.read().decode("utf-8", errors="replace")

    return parse_arxiv_feed(xml_text)


def fetch_arxiv_by_window(
    *,
    start_datetime: datetime,
    end_datetime: datetime,
    categories: list[str],
    focus_terms: list[str] | None = None,
    focus_mode: str = "any",
    max_results: int = 50,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """Fetch arXiv papers submitted within a UTC datetime window."""
    start = _to_arxiv_timestamp(start_datetime)
    end = _to_arxiv_timestamp(end_datetime)
    date_query = f"submittedDate:[{start} TO {end}]"
    search_query = _build_search_query(
        date_query=date_query,
        categories=categories,
        focus_terms=focus_terms or [],
        focus_mode=focus_mode,
    )

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max(1, int(max_results)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=DEFAULT_HEADERS)

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        xml_text = response.read().decode("utf-8", errors="replace")

    return parse_arxiv_feed(xml_text)


def fetch_arxiv_latest(
    *,
    categories: list[str],
    focus_terms: list[str] | None = None,
    focus_mode: str = "any",
    max_results: int = 50,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """Fetch latest arXiv papers by categories when strict date has no results."""
    search_query = _build_search_query(
        categories=categories,
        focus_terms=focus_terms or [],
        focus_mode=focus_mode,
    )
    if not search_query:
        search_query = "cat:cs.AI"

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max(1, int(max_results)),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=DEFAULT_HEADERS)

    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        xml_text = response.read().decode("utf-8", errors="replace")

    return parse_arxiv_feed(xml_text)


def parse_arxiv_feed(xml_text: str) -> list[dict[str, object]]:
    """Parse Atom XML feed into raw connector records."""
    root = ET.fromstring(xml_text)
    entries = root.findall(".//{*}entry")
    papers: list[dict[str, object]] = []

    for entry in entries:
        paper_id_raw = _text(entry, "{*}id")
        paper_id = paper_id_raw.rsplit("/", 1)[-1] if paper_id_raw else ""
        title = _text(entry, "{*}title")
        abstract = _text(entry, "{*}summary")
        published = _text(entry, "{*}published")
        authors = [a.text.strip() for a in entry.findall("{*}author/{*}name") if a.text]
        url = _text(entry, "{*}id")
        pdf_url = ""

        for link in entry.findall("{*}link"):
            href = link.attrib.get("href", "")
            link_type = link.attrib.get("type", "")
            title_attr = link.attrib.get("title", "")
            if link_type == "application/pdf" or title_attr.lower() == "pdf":
                pdf_url = href
                break

        papers.append(
            {
                "source_tag": "arXiv",
                "source_id": paper_id,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "published_at": published,
                "url": url,
                "pdf_url": pdf_url,
                "source_popularity": 0.0,
            }
        )

    return papers


def _text(node: ET.Element, path: str) -> str:
    match = node.find(path)
    if match is None or match.text is None:
        return ""
    return " ".join(match.text.split())


def _build_search_query(
    *,
    date_query: str | None = None,
    categories: list[str] | None = None,
    focus_terms: list[str] | None = None,
    focus_mode: str = "any",
) -> str:
    clauses: list[str] = []

    if date_query:
        clauses.append(f"({date_query})")

    category_query = " OR ".join(
        f"cat:{cat.strip()}" for cat in (categories or []) if str(cat).strip()
    )
    if category_query:
        clauses.append(f"({category_query})")

    focus_clause = _build_focus_clause(focus_terms or [], focus_mode=focus_mode)
    if focus_clause:
        clauses.append(focus_clause)

    return " AND ".join(clauses)


def _build_focus_clause(focus_terms: list[str], *, focus_mode: str) -> str:
    terms = [str(t).strip() for t in focus_terms if str(t).strip()]
    if not terms:
        return ""

    token = " AND " if focus_mode.lower() == "all" else " OR "
    per_term: list[str] = []
    for term in terms:
        normalized = term.replace('"', " ").strip()
        if not normalized:
            continue
        escaped = normalized.replace("\\", "\\\\")
        q = f'"{escaped}"'
        per_term.append(f"(ti:{q} OR abs:{q})")

    if not per_term:
        return ""

    return f"({token.join(per_term)})"


def _to_arxiv_timestamp(value: datetime) -> str:
    dt = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return dt.strftime("%Y%m%d%H%M")
