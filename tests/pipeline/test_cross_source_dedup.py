import json
from pathlib import Path

from src.pipeline.dedup import deduplicate_papers
from src.pipeline.normalize import normalize_records


def test_cross_source_dedup() -> None:
    raw_items = json.loads(Path("tests/fixtures/pipeline/duplicates_cross_source.json").read_text(encoding="utf-8"))["items"]
    normalized = normalize_records(raw_items)

    deduped, audits = deduplicate_papers(normalized)

    assert len(normalized) == 3
    assert len(deduped) == 2
    assert len(audits) == 1
    assert sorted(deduped[0].source_list + deduped[1].source_list)
