"""Dual-track selection for For You and Trending Now outputs."""

from __future__ import annotations

from datetime import datetime, timezone

from src.models import NormalizedPaper


def select_dual_track(
    papers: list[NormalizedPaper],
    for_you_n: int = 5,
    trending_n: int = 5,
) -> dict[str, list[NormalizedPaper]]:
    """Select top papers for both tracks with deterministic ordering."""
    for_you_sorted = sorted(papers, key=_for_you_sort_key)
    trending_sorted = sorted(papers, key=_trending_sort_key)

    for_you = _take_with_source_diversity(for_you_sorted, for_you_n)
    selected_ids = {p.paper_id for p in for_you}

    trending: list[NormalizedPaper] = []
    for paper in trending_sorted:
        if paper.paper_id in selected_ids:
            continue
        trending.append(paper)
        if len(trending) >= trending_n:
            break

    return {"For You": for_you, "Trending Now": trending}


def _for_you_sort_key(paper: NormalizedPaper) -> tuple[float, str, str]:
    return (-paper.for_you_score, -_published_epoch(paper.published_at), paper.paper_id)


def _trending_sort_key(paper: NormalizedPaper) -> tuple[float, str, str]:
    return (-paper.trending_score, -_published_epoch(paper.published_at), paper.paper_id)


def _published_epoch(published_at: str) -> float:
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _take_with_source_diversity(candidates: list[NormalizedPaper], limit: int) -> list[NormalizedPaper]:
    if limit <= 0:
        return []

    result: list[NormalizedPaper] = []
    source_seen: set[str] = set()

    # First pass: maximize source diversity.
    for paper in candidates:
        if paper.source_tag in source_seen:
            continue
        result.append(paper)
        source_seen.add(paper.source_tag)
        if len(result) >= limit:
            return result

    # Second pass: fill remaining slots.
    selected_ids = {p.paper_id for p in result}
    for paper in candidates:
        if paper.paper_id in selected_ids:
            continue
        result.append(paper)
        if len(result) >= limit:
            break

    return result
