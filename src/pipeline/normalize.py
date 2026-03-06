"""Normalization utilities for raw connector payloads."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from src.models import NormalizedPaper


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_records(raw_records: list[dict[str, object]]) -> list[NormalizedPaper]:
    """Convert source-specific raw records into canonical NormalizedPaper objects."""
    papers: list[NormalizedPaper] = []
    for record in raw_records:
        source_tag = str(record.get("source_tag", "")).strip()
        source_id = str(record.get("source_id", "")).strip()
        title = str(record.get("title", "")).strip()
        abstract = str(record.get("abstract", "")).strip()
        authors = [str(a).strip() for a in list(record.get("authors", [])) if str(a).strip()]
        published_at = _to_iso8601(str(record.get("published_at", "")).strip())
        source_url = canonicalize_url(str(record.get("url", "")).strip())
        source_popularity = float(record.get("source_popularity", 0.0) or 0.0)

        paper_id = _build_paper_id(source_tag, source_id, title, source_url)

        papers.append(
            NormalizedPaper(
                paper_id=paper_id,
                title_en=title,
                abstract_raw=abstract,
                authors=authors,
                published_at=published_at,
                source_tag=source_tag,
                source_url=source_url,
                source_list=[source_tag] if source_tag else [],
                source_popularity=source_popularity,
            )
        )

    return papers


def normalize_title_for_hash(title: str) -> str:
    return _WHITESPACE_RE.sub(" ", title.strip().lower())


def canonicalize_url(url: str) -> str:
    normalized = url.strip()
    if normalized.endswith("/"):
        return normalized[:-1]
    return normalized


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title_for_hash(title).encode("utf-8")).hexdigest()[:16]


def _build_paper_id(source_tag: str, source_id: str, title: str, source_url: str) -> str:
    tag = source_tag.replace(" ", "").lower()
    if source_id:
        return f"{tag}:{source_id}"
    if source_url:
        return f"{tag}:url:{hashlib.sha256(source_url.encode('utf-8')).hexdigest()[:16]}"
    return f"{tag}:title:{title_hash(title)}"


def _to_iso8601(value: str) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()

    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return datetime.now(timezone.utc).isoformat()

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
