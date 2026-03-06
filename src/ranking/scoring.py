"""Ranking and score computation for For You / Trending tracks."""

from __future__ import annotations

from datetime import datetime, timezone

from src.models import NormalizedPaper


def score_papers(
    papers: list[NormalizedPaper],
    keywords: list[str],
    now_utc: datetime | None = None,
    weights: dict[str, float] | None = None,
) -> list[NormalizedPaper]:
    """Apply MVP formulas for interest and trending scores."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    if not papers:
        return papers

    recency_values: list[float] = []
    popularity_values: list[float] = []

    for paper in papers:
        age_days = _age_in_days(paper.published_at, now_utc)
        recency = 1.0 / (1.0 + age_days)
        recency_values.append(recency)
        popularity_values.append(max(0.0, paper.source_popularity))

    recency_norm = _normalize_list(recency_values)
    popularity_norm = _normalize_list(popularity_values)

    if weights is None:
        weights = {"interest": 0.65, "freshness": 0.20, "trending": 0.15}

    w_interest = float(weights.get("interest", 0.65))
    w_freshness = float(weights.get("freshness", 0.20))
    w_trending = float(weights.get("trending", 0.15))
    total = w_interest + w_freshness + w_trending
    if total <= 0:
        w_interest, w_freshness, w_trending = 0.65, 0.20, 0.15
        total = 1.0

    w_interest /= total
    w_freshness /= total
    w_trending /= total

    for idx, paper in enumerate(papers):
        cross_source_boost = 1.0 if len(set(paper.source_list)) > 1 else 0.0
        paper.trending_score = (
            0.50 * popularity_norm[idx] + 0.40 * recency_norm[idx] + 0.10 * cross_source_boost
        )

        paper.interest_relevance = _interest_score(paper, keywords)
        paper.for_you_score = (
            w_interest * paper.interest_relevance
            + w_freshness * recency_norm[idx]
            + w_trending * paper.trending_score
        )

    return papers


def _interest_score(paper: NormalizedPaper, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{paper.title_en} {paper.abstract_raw}".lower()
    hits = 0
    for keyword in keywords:
        if keyword.strip().lower() and keyword.strip().lower() in text:
            hits += 1
    return min(1.0, hits / max(1, len(keywords)))


def _age_in_days(published_iso: str, now_utc: datetime) -> float:
    try:
        dt = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = now_utc - dt.astimezone(timezone.utc)
        return max(0.0, age.total_seconds() / 86400.0)
    except ValueError:
        return 365.0


def _normalize_list(values: list[float]) -> list[float]:
    if not values:
        return []
    min_v = min(values)
    max_v = max(values)
    if max_v == min_v:
        return [1.0 for _ in values]
    return [(v - min_v) / (max_v - min_v) for v in values]
