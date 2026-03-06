import json
from pathlib import Path

from src.delivery.feishu_adapter import (
    IdempotencyStore,
    _extract_feishu_app_code,
    build_post_payload,
    chunk_entries_by_limits,
    flatten_section_entries,
    format_feishu_text_chunk,
    webhook_host_from_url,
    send_chunks_with_retry,
)
from src.models import RUN_FAILED_DELIVERY, RUN_SUCCESS


def test_retryable_codes_retried_and_success() -> None:
    statuses = json.loads(Path("tests/fixtures/delivery/feishu_http_retryable.json").read_text(encoding="utf-8"))["status_codes"]
    attempts = {"count": 0}

    def send_fn(_chunk: list[dict[str, str]]) -> int:
        idx = attempts["count"]
        attempts["count"] += 1
        if idx < len(statuses):
            return int(statuses[idx])
        return 200

    result = send_chunks_with_retry(
        run_id="run-001",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a", "b"],
        chunks=[[{"title_en": "A", "summary_zh": "摘要", "source_tag": "arXiv", "source_url": "u"}]],
        send_fn=send_fn,
        store=IdempotencyStore(),
    )

    assert result["run_status"] == RUN_SUCCESS
    assert attempts["count"] == 3


def test_non_retryable_not_retried_and_failed_logged() -> None:
    statuses = json.loads(Path("tests/fixtures/delivery/feishu_http_non_retryable.json").read_text(encoding="utf-8"))["status_codes"]
    attempts = {"count": 0}

    def send_fn(_chunk: list[dict[str, str]]) -> int:
        attempts["count"] += 1
        return int(statuses[0])

    result = send_chunks_with_retry(
        run_id="run-002",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a", "b"],
        chunks=[[{"title_en": "A", "summary_zh": "摘要", "source_tag": "arXiv", "source_url": "u"}]],
        send_fn=send_fn,
        store=IdempotencyStore(),
    )

    assert result["run_status"] == RUN_FAILED_DELIVERY
    assert attempts["count"] == 1
    event = result["events"][0]
    assert event["run_id"] == "run-002"
    assert event["error_code"].startswith("HTTP_")


def test_exhausted_retryable_transitions_to_failed_delivery() -> None:
    attempts = {"count": 0}

    def send_fn(_chunk: list[dict[str, str]]) -> int:
        attempts["count"] += 1
        return 503

    result = send_chunks_with_retry(
        run_id="run-003",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a", "b"],
        chunks=[[{"title_en": "A", "summary_zh": "摘要", "source_tag": "arXiv", "source_url": "u"}]],
        send_fn=send_fn,
        store=IdempotencyStore(),
    )

    assert result["run_status"] == RUN_FAILED_DELIVERY
    assert attempts["count"] == 4  # first try + 3 retries


def test_payload_title_and_chunk_suffix_contract() -> None:
    payload_single = build_post_payload(
        run_date="2026-03-03",
        run_id="20260303-0900-abcd",
        sections={"For You": [], "Trending Now": []},
    )
    title_single = payload_single["content"]["post"]["zh_cn"]["title"]
    assert title_single == "Daily Papers 2026-03-03 (Run: 20260303-0900-abcd)"

    payload_multi = build_post_payload(
        run_date="2026-03-03",
        run_id="20260303-0900-abcd",
        sections={"For You": [], "Trending Now": []},
        chunk_index=2,
        chunk_total=3,
    )
    title_multi = payload_multi["content"]["post"]["zh_cn"]["title"]
    assert title_multi.endswith("[2/3]")


def test_chunking_respects_entry_count_and_char_limit() -> None:
    sections = {
        "For You": [
            {"title_en": "A", "summary_zh": "中" * 60, "source_tag": "arXiv", "source_url": "u1"},
            {"title_en": "B", "summary_zh": "中" * 60, "source_tag": "arXiv", "source_url": "u2"},
        ],
        "Trending Now": [
            {"title_en": "C", "summary_zh": "中" * 60, "source_tag": "HF Daily", "source_url": "u3"},
            {"title_en": "D", "summary_zh": "中" * 60, "source_tag": "HF Daily", "source_url": "u4"},
        ],
    }
    flat = flatten_section_entries(sections)
    chunks = chunk_entries_by_limits(flat, max_entries=2, max_chars=150)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 2 for chunk in chunks)


def test_run_level_suppression_skips_second_send() -> None:
    store = IdempotencyStore()
    calls = {"count": 0}

    def send_fn(_chunk: list[dict[str, str]]) -> int:
        calls["count"] += 1
        return 200

    chunks = [[{"title_en": "A", "summary_zh": "摘要", "source_tag": "arXiv", "source_url": "u"}]]

    first = send_chunks_with_retry(
        run_id="run-004",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a"],
        chunks=chunks,
        send_fn=send_fn,
        store=store,
    )
    second = send_chunks_with_retry(
        run_id="run-004",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a"],
        chunks=chunks,
        send_fn=send_fn,
        store=store,
    )

    assert first["run_status"] == RUN_SUCCESS
    assert second["skipped_all"] is True
    assert calls["count"] == 1


def test_idempotency_persists_to_jsonl(tmp_path) -> None:
    jsonl_path = tmp_path / "delivery_idempotency.jsonl"
    store_first = IdempotencyStore(jsonl_path=str(jsonl_path))
    calls = {"count": 0}

    def send_fn(_chunk: list[dict[str, str]]) -> int:
        calls["count"] += 1
        return 200

    chunks = [[{"title_en": "A", "summary_zh": "摘要", "source_tag": "arXiv", "source_url": "u"}]]

    send_chunks_with_retry(
        run_id="run-005",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a"],
        chunks=chunks,
        send_fn=send_fn,
        store=store_first,
    )

    store_second = IdempotencyStore(jsonl_path=str(jsonl_path))
    result = send_chunks_with_retry(
        run_id="run-005",
        run_date="2026-03-03",
        webhook_host="open.feishu.test",
        selected_paper_ids=["a"],
        chunks=chunks,
        send_fn=send_fn,
        store=store_second,
    )

    assert result["skipped_all"] is True
    assert calls["count"] == 1


def test_webhook_host_from_url() -> None:
    assert webhook_host_from_url("") == "open.feishu.local"
    assert webhook_host_from_url("https://open.feishu.cn/open-apis/bot/v2/hook/abc") == "open.feishu.cn"


def test_format_feishu_text_chunk_includes_required_fields() -> None:
    text = format_feishu_text_chunk(
        run_date="2026-03-03",
        run_id="run-006",
        chunk=[
            {
                "section": "For You",
                "title_en": "Paper A",
                "summary_zh": "这是摘要",
                "source_tag": "arXiv",
                "source_url": "http://arxiv.org/abs/0000.00000",
            }
        ],
    )
    assert "Daily Papers 2026-03-03 (Run: run-006)" in text
    assert "[For You]" in text
    assert "Paper A" in text
    assert "Link: http://arxiv.org/abs/0000.00000" in text


def test_extract_feishu_app_code() -> None:
    assert _extract_feishu_app_code('{"code":0,"msg":"success"}') == 0
    assert _extract_feishu_app_code('{"code":19024,"msg":"frequency limit"}') == 429
    assert _extract_feishu_app_code('{"code":10001,"msg":"bad request"}') == 422
