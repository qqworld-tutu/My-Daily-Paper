"""Cross-source deduplication logic with audit logs."""

from __future__ import annotations

from src.models import NormalizedPaper
from src.pipeline.normalize import canonicalize_url, normalize_title_for_hash


AuditRow = dict[str, str]


def deduplicate_papers(papers: list[NormalizedPaper]) -> tuple[list[NormalizedPaper], list[AuditRow]]:
    """Deduplicate records by contract key precedence.

    Key precedence:
    1) exact canonical arXiv id
    2) canonical URL
    3) normalized title hash + first author
    """
    deduped: list[NormalizedPaper] = []
    audits: list[AuditRow] = []

    index_by_key: dict[str, int] = {}

    for paper in papers:
        winner_idx = _find_existing_index(paper, index_by_key)
        if winner_idx is None:
            deduped.append(paper)
            _register_keys(paper, len(deduped) - 1, index_by_key)
            continue

        winner = deduped[winner_idx]
        _merge_in_place(winner, paper)
        audits.append(
            {
                "winning_key": _primary_key(winner),
                "loser_key": _primary_key(paper),
                "reason": "key_collision",
            }
        )
        _register_keys(winner, winner_idx, index_by_key)

    return deduped, audits


def _find_existing_index(paper: NormalizedPaper, index_by_key: dict[str, int]) -> int | None:
    for key in _all_keys(paper):
        if key in index_by_key:
            return index_by_key[key]
    return None


def _register_keys(paper: NormalizedPaper, idx: int, index_by_key: dict[str, int]) -> None:
    for key in _all_keys(paper):
        index_by_key[key] = idx


def _all_keys(paper: NormalizedPaper) -> list[str]:
    keys: list[str] = []
    arxiv_key = _arxiv_key(paper)
    if arxiv_key:
        keys.append(arxiv_key)
    if paper.source_url:
        keys.append(f"url:{canonicalize_url(paper.source_url)}")

    first_author = paper.authors[0].strip().lower() if paper.authors else ""
    title_norm = normalize_title_for_hash(paper.title_en)
    keys.append(f"title_author:{title_norm}|{first_author}")
    return keys


def _arxiv_key(paper: NormalizedPaper) -> str:
    value = paper.paper_id
    if value.startswith("arxiv:"):
        return f"arxiv:{value.split(':', 1)[1]}"
    if "arxiv.org" in paper.source_url and "/abs/" in paper.source_url:
        return f"arxiv:{paper.source_url.rsplit('/abs/', 1)[-1]}"
    return ""


def _merge_in_place(winner: NormalizedPaper, duplicate: NormalizedPaper) -> None:
    sources = sorted(set(winner.source_list + duplicate.source_list))
    winner.source_list = sources

    if len(duplicate.abstract_raw) > len(winner.abstract_raw):
        winner.abstract_raw = duplicate.abstract_raw

    if duplicate.source_popularity > winner.source_popularity:
        winner.source_popularity = duplicate.source_popularity


def _primary_key(paper: NormalizedPaper) -> str:
    keys = _all_keys(paper)
    return keys[0] if keys else paper.paper_id
