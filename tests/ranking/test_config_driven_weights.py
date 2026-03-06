import json
from pathlib import Path

from src.pipeline.normalize import normalize_records
from src.ranking.scoring import score_papers


def _load_weights(path: str) -> dict[str, float]:
    text = Path(path).read_text(encoding="utf-8")
    weights: dict[str, float] = {}
    in_weights = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip() == "weights:":
            in_weights = True
            continue
        if in_weights and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            weights[key.strip()] = float(value.strip())
        elif in_weights and line and not line.startswith(" "):
            break

    return weights


def test_config_driven_weights_change_order() -> None:
    raw_items = json.loads(Path("tests/fixtures/ranking/ranking_input_fixed.json").read_text(encoding="utf-8"))["items"]
    keywords = ["retrieval", "transformer"]

    papers_a = normalize_records(raw_items)
    papers_b = normalize_records(raw_items)

    weights_a = _load_weights("tests/fixtures/ranking/config_variant_a.yaml")
    weights_b = _load_weights("tests/fixtures/ranking/config_variant_b.yaml")

    score_papers(papers_a, keywords, weights=weights_a)
    score_papers(papers_b, keywords, weights=weights_b)

    sorted_a = [p.paper_id for p in sorted(papers_a, key=lambda p: (-p.for_you_score, p.paper_id))]
    sorted_b = [p.paper_id for p in sorted(papers_b, key=lambda p: (-p.for_you_score, p.paper_id))]

    assert sorted_a != sorted_b

    # Deterministic for same input/config
    papers_c = normalize_records(raw_items)
    score_papers(papers_c, keywords, weights=weights_a)
    sorted_c = [p.paper_id for p in sorted(papers_c, key=lambda p: (-p.for_you_score, p.paper_id))]
    assert sorted_a == sorted_c
