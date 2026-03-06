"""Core data models for daily paper push MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedPaper:
    """Canonical paper model used across pipeline stages."""

    paper_id: str
    title_en: str
    abstract_raw: str
    authors: list[str]
    published_at: str
    source_tag: str
    source_url: str
    source_list: list[str] = field(default_factory=list)
    summary_zh: str = ""
    source_popularity: float = 0.0
    interest_relevance: float = 0.0
    trending_score: float = 0.0
    for_you_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title_en": self.title_en,
            "abstract_raw": self.abstract_raw,
            "summary_zh": self.summary_zh,
            "authors": list(self.authors),
            "published_at": self.published_at,
            "source_tag": self.source_tag,
            "source_url": self.source_url,
            "source_popularity": self.source_popularity,
            "interest_relevance": self.interest_relevance,
            "trending_score": self.trending_score,
            "for_you_score": self.for_you_score,
            "source_list": list(self.source_list),
        }


RUN_SUCCESS = "SUCCESS"
RUN_FAILED_SOURCE = "FAILED_SOURCE"
RUN_FAILED_DELIVERY = "FAILED_DELIVERY"
RUN_SKIPPED_LOCKED = "SKIPPED_LOCKED"
