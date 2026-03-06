from datetime import UTC, datetime

from src.connectors.arxiv_connector import _build_search_query, _to_arxiv_timestamp


def test_build_search_query_with_focus_any() -> None:
    query = _build_search_query(
        date_query="submittedDate:[202603030000 TO 202603032359]",
        categories=["cs.AI", "cs.CL"],
        focus_terms=["reinforcement learning", "agent"],
        focus_mode="any",
    )

    assert "(submittedDate:[202603030000 TO 202603032359])" in query
    assert "(cat:cs.AI OR cat:cs.CL)" in query
    assert '(ti:"reinforcement learning" OR abs:"reinforcement learning")' in query
    assert '(ti:"agent" OR abs:"agent")' in query
    assert " OR " in query


def test_build_search_query_with_focus_all() -> None:
    query = _build_search_query(
        categories=["cs.LG"],
        focus_terms=["reasoning", "natural language"],
        focus_mode="all",
    )

    assert "(cat:cs.LG)" in query
    assert (
        '(ti:"reasoning" OR abs:"reasoning") AND '
        '(ti:"natural language" OR abs:"natural language")'
    ) in query


def test_to_arxiv_timestamp_uses_utc_minute_precision() -> None:
    dt = datetime(2026, 3, 6, 1, 23, 45, tzinfo=UTC)
    assert _to_arxiv_timestamp(dt) == "202603060123"
