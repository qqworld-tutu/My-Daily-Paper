import json
from pathlib import Path

from src.scheduler.daily_job import run_once


REQUIRED_FIELDS = {"title_en", "summary_zh", "source_tag", "source_url", "paper_id"}


def test_push_entry_schema_and_language(tmp_path) -> None:
    arxiv_xml = Path("tests/fixtures/sources/arxiv_sample_feed.xml").read_text(encoding="utf-8")
    hf_json = Path("tests/fixtures/sources/hf_daily_sample.json").read_text(encoding="utf-8")

    result = run_once(
        arxiv_xml=arxiv_xml,
        hf_json=hf_json,
        keywords=["transformer", "diffusion", "retrieval", "vision"],
        source_success_mode="strict_both",
        lock_path=str(tmp_path / "run.lock"),
    )

    entries = result.sections["For You"] + result.sections["Trending Now"]
    assert entries

    for entry in entries:
        assert REQUIRED_FIELDS.issubset(entry.keys())
        summary = str(entry["summary_zh"])
        sentences = [x for x in summary.replace("！", "。").replace("?", "。").split("。") if x.strip()]
        assert 2 <= len(sentences) <= 4
        assert len(summary) >= 30
        assert "摘要" in summary


def test_invalid_fixture_missing_fields() -> None:
    valid = json.loads(Path("tests/fixtures/contracts/push_entry_valid.json").read_text(encoding="utf-8"))
    invalid = json.loads(
        Path("tests/fixtures/contracts/push_entry_invalid_missing_fields.json").read_text(encoding="utf-8")
    )

    assert REQUIRED_FIELDS.issubset(valid.keys())
    assert not REQUIRED_FIELDS.issubset(invalid.keys())
