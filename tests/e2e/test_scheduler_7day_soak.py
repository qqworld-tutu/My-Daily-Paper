from datetime import UTC, datetime
import json
from pathlib import Path
import re

from src.scheduler.daily_job import (
    generate_run_id,
    should_run_now,
    simulate_7day_soak,
    with_pipeline_step_retry,
)


def test_scheduler_7day_soak() -> None:
    fixture = Path("tests/fixtures/e2e/soak_7days_events.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    events = payload["runs"]

    result = simulate_7day_soak(events)

    assert len(events) == 7
    assert result["all_success"] is True
    assert result["success_rate"] == 1.0
    assert result["max_drift"] <= 120
    assert result["p95_runtime"] <= 900


def test_should_run_now_for_0900_asia_shanghai() -> None:
    due_time = datetime(2026, 3, 3, 1, 0, tzinfo=UTC)  # +8 => 09:00
    not_due_time = datetime(2026, 3, 3, 2, 0, tzinfo=UTC)  # +8 => 10:00

    assert should_run_now(due_time, "09:00", "Asia/Shanghai") is True
    assert should_run_now(not_due_time, "09:00", "Asia/Shanghai") is False


def test_generate_run_id_format() -> None:
    run_id = generate_run_id(datetime(2026, 3, 3, 1, 2, tzinfo=UTC), repo_root=".")
    assert re.match(r"^20260303-0102-[a-zA-Z0-9]+$", run_id)


def test_pipeline_step_retry_backoff_sequence() -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def flaky() -> str:
        calls["count"] += 1
        raise RuntimeError("temporary failure")

    result = with_pipeline_step_retry(
        flaky,
        fallback="fallback",
        sleep_fn=lambda seconds: sleeps.append(seconds),
    )

    assert result == "fallback"
    assert calls["count"] == 4  # first attempt + 3 retries
    assert sleeps == [60.0, 180.0, 600.0]
