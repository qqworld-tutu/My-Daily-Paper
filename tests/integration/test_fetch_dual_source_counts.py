from pathlib import Path

from src.models import RUN_FAILED_SOURCE, RUN_SKIPPED_LOCKED, RUN_SUCCESS
from src.scheduler.daily_job import run_once


def test_fetch_dual_source_counts(tmp_path) -> None:
    arxiv_xml = Path("tests/fixtures/sources/arxiv_sample_feed.xml").read_text(encoding="utf-8")
    hf_json = Path("tests/fixtures/sources/hf_daily_sample.json").read_text(encoding="utf-8")

    result = run_once(
        arxiv_xml=arxiv_xml,
        hf_json=hf_json,
        keywords=["transformer", "diffusion", "retrieval"],
        source_success_mode="strict_both",
        lock_path=str(tmp_path / "run.lock"),
    )

    assert result.run_status == RUN_SUCCESS
    assert result.fetched_counts_by_source["arXiv"] >= 1
    assert result.fetched_counts_by_source["HF Daily"] >= 1


def test_source_failure_blocks_push_attempts(tmp_path) -> None:
    arxiv_xml = Path("tests/fixtures/sources/arxiv_sample_feed.xml").read_text(encoding="utf-8")
    hf_json = "{\"items\": []}"
    attempts = {"count": 0}

    def send_fn(_chunk: list[dict[str, str]]) -> int:
        attempts["count"] += 1
        return 200

    result = run_once(
        arxiv_xml=arxiv_xml,
        hf_json=hf_json,
        keywords=["transformer"],
        source_success_mode="strict_both",
        send_fn=send_fn,
        lock_path=str(tmp_path / "run.lock"),
    )

    assert result.run_status == RUN_FAILED_SOURCE
    assert result.push_attempts == 0
    assert attempts["count"] == 0


def test_lock_conflict_returns_skipped_locked(tmp_path) -> None:
    arxiv_xml = Path("tests/fixtures/sources/arxiv_sample_feed.xml").read_text(encoding="utf-8")
    hf_json = Path("tests/fixtures/sources/hf_daily_sample.json").read_text(encoding="utf-8")
    lock_path = tmp_path / "run.lock"
    lock_path.write_text("busy", encoding="utf-8")

    result = run_once(
        arxiv_xml=arxiv_xml,
        hf_json=hf_json,
        keywords=["transformer"],
        source_success_mode="strict_both",
        lock_path=str(lock_path),
    )

    assert result.run_status == RUN_SKIPPED_LOCKED
    assert result.push_attempts == 0
