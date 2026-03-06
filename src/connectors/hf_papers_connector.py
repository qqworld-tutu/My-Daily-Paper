"""Hugging Face Daily Papers parser and connector helpers."""

from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import date, timedelta


HF_BASE_URL = "https://huggingface.co"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_HEADERS = {
    "User-Agent": "daily-paper-push-mvp/0.1 (+https://github.com)",
    "Accept": "text/html,application/xhtml+xml",
}


def fetch_hf_daily_by_date(
    *,
    target_date: date,
    max_results: int = 50,
    fallback_days: int = 3,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, object]]:
    """Fetch HF daily papers from date pages; fallback to previous days if empty."""
    attempts = max(0, int(fallback_days)) + 1
    for offset in range(attempts):
        cur_date = target_date - timedelta(days=offset)
        daily = _fetch_single_day(cur_date, max_results=max_results, timeout_seconds=timeout_seconds)
        if daily:
            return daily
    return []


def _fetch_single_day(
    target_date: date,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[dict[str, object]]:
    url = f"{HF_BASE_URL}/papers/date/{target_date.isoformat()}"
    request = urllib.request.Request(url, headers=DEFAULT_HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            html_text = response.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    papers = parse_hf_daily_html(html_text)
    if max_results > 0:
        return papers[:max_results]
    return papers


def parse_hf_daily_html(html_text: str) -> list[dict[str, object]]:
    """Parse huggingface daily page HTML into raw records."""
    props_candidates = re.findall(r'data-props="([^"]+)"', html_text)
    payload: dict[str, object] | None = None

    for raw in props_candidates:
        unescaped = html.unescape(raw)
        if "dailyPapers" not in unescaped:
            continue
        try:
            obj = json.loads(unescaped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "dailyPapers" in obj:
            payload = obj
            break

    if payload is None:
        return []

    items = payload.get("dailyPapers", [])
    if not isinstance(items, list):
        return []

    papers: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        paper_info = item.get("paper") if isinstance(item.get("paper"), dict) else {}

        source_id = str(paper_info.get("id", item.get("id", ""))).strip()
        title = str(item.get("title", paper_info.get("title", ""))).strip()
        abstract = str(item.get("summary", paper_info.get("summary", ""))).strip()
        published_at = str(
            paper_info.get("publishedAt", item.get("publishedAt", item.get("date", "")))
        ).strip()

        authors_data = paper_info.get("authors", [])
        authors: list[str] = []
        if isinstance(authors_data, list):
            for a in authors_data:
                if isinstance(a, dict):
                    name = str(a.get("name", "")).strip()
                    if name:
                        authors.append(name)
                else:
                    txt = str(a).strip()
                    if txt:
                        authors.append(txt)

        url = f"{HF_BASE_URL}/papers/{source_id}" if source_id else ""
        popularity = _extract_popularity(item, paper_info)

        papers.append(
            {
                "source_tag": "HF Daily",
                "source_id": source_id,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "published_at": published_at,
                "url": url,
                "source_popularity": popularity,
            }
        )

    return papers


def parse_hf_daily_payload(json_text: str) -> list[dict[str, object]]:
    """Parse HF daily papers JSON payload into raw connector records."""
    payload = json.loads(json_text)
    if isinstance(payload, dict):
        items = payload.get("items", [])
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    papers: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        papers.append(
            {
                "source_tag": "HF Daily",
                "source_id": str(item.get("id", "")).strip(),
                "title": str(item.get("title", "")).strip(),
                "abstract": str(item.get("abstract", "")).strip(),
                "authors": list(item.get("authors", [])),
                "published_at": str(item.get("published_at", item.get("date", ""))).strip(),
                "url": str(item.get("url", "")).strip(),
                "source_popularity": float(item.get("popularity", 0.0) or 0.0),
            }
        )
    return papers


def _extract_popularity(item: dict[str, object], paper_info: dict[str, object]) -> float:
    for key in ["upvotes", "votes", "score", "likes", "numVotes"]:
        value = item.get(key, paper_info.get(key))
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0
