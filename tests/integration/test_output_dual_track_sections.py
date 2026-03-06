from pathlib import Path

from src.models import NormalizedPaper
from src.models import RUN_SUCCESS
from src.scheduler.daily_job import run_once
from src.ranking.selection import select_dual_track


def test_output_dual_track_sections(tmp_path) -> None:
    arxiv_xml = Path("tests/fixtures/sources/arxiv_sample_feed.xml").read_text(encoding="utf-8")
    hf_json = Path("tests/fixtures/sources/hf_daily_sample.json").read_text(encoding="utf-8")

    result = run_once(
        arxiv_xml=arxiv_xml,
        hf_json=hf_json,
        keywords=["transformer", "diffusion", "retrieval", "vision"],
        source_success_mode="strict_both",
        lock_path=str(tmp_path / "run.lock"),
    )

    assert result.run_status == RUN_SUCCESS
    assert list(result.sections.keys()) == ["For You", "Trending Now"]
    assert len(result.sections["For You"]) >= 3
    assert len(result.sections["Trending Now"]) >= 3


def test_tie_break_prefers_newer_paper() -> None:
    older = NormalizedPaper(
        paper_id="p-old",
        title_en="Old",
        abstract_raw="a",
        authors=["a"],
        published_at="2026-01-01T00:00:00+00:00",
        source_tag="arXiv",
        source_url="https://arxiv.org/abs/p-old",
        source_list=["arXiv"],
        for_you_score=0.9,
        trending_score=0.9,
    )
    newer = NormalizedPaper(
        paper_id="p-new",
        title_en="New",
        abstract_raw="b",
        authors=["b"],
        published_at="2026-02-01T00:00:00+00:00",
        source_tag="HF Daily",
        source_url="https://huggingface.co/papers/p-new",
        source_list=["HF Daily"],
        for_you_score=0.9,
        trending_score=0.9,
    )

    result = select_dual_track([older, newer], for_you_n=1, trending_n=1)
    assert result["For You"][0].paper_id == "p-new"
