from datetime import UTC, datetime, timedelta

from src.scheduler.daily_job import resolve_arxiv_window, selected_paper_ids_from_sections


def test_resolve_arxiv_window_defaults_to_one_schedule_interval() -> None:
    now = datetime(2026, 3, 6, 1, 0, tzinfo=UTC)
    start, end = resolve_arxiv_window(
        now_utc=now,
        daily_time="09:00",
        tz_name="Asia/Shanghai",
        scheduler_state={},
    )

    assert end == now
    assert start == now - timedelta(days=1)


def test_resolve_arxiv_window_uses_last_success_state() -> None:
    now = datetime(2026, 3, 6, 1, 0, tzinfo=UTC)
    last_success = datetime(2026, 3, 5, 1, 0, tzinfo=UTC)
    start, end = resolve_arxiv_window(
        now_utc=now,
        daily_time="09:00",
        tz_name="Asia/Shanghai",
        scheduler_state={"last_success_at_utc": last_success.isoformat()},
    )

    assert start == last_success
    assert end == now


def test_selected_paper_ids_from_sections() -> None:
    ids = selected_paper_ids_from_sections(
        {
            "For You": [{"paper_id": "a"}, {"paper_id": "b"}],
            "Trending Now": [{"paper_id": "c"}],
        }
    )
    assert ids == ["a", "b", "c"]
