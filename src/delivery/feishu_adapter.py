"""Feishu payload formatting, chunking and retry/idempotency logic."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.models import RUN_FAILED_DELIVERY, RUN_SUCCESS


RETRYABLE_CODES = {429, 500, 502, 503, 504}
NON_RETRYABLE_CODES = {400, 401, 403, 404, 413, 422}


@dataclass
class IdempotencyStore:
    run_status: dict[str, str] = field(default_factory=dict)
    chunk_status: dict[str, str] = field(default_factory=dict)
    jsonl_path: str | None = None

    def __post_init__(self) -> None:
        if self.jsonl_path:
            self._load_from_jsonl(self.jsonl_path)

    def set_run_status(self, key: str, status: str) -> None:
        self.run_status[key] = status
        self._append_jsonl("run", key, status)

    def set_chunk_status(self, key: str, status: str) -> None:
        self.chunk_status[key] = status
        self._append_jsonl("chunk", key, status)

    def _load_from_jsonl(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("kind")
            key = obj.get("key")
            status = obj.get("status")
            if not isinstance(key, str) or not isinstance(status, str):
                continue
            if kind == "run":
                self.run_status[key] = status
            elif kind == "chunk":
                self.chunk_status[key] = status

    def _append_jsonl(self, kind: str, key: str, status: str) -> None:
        if not self.jsonl_path:
            return
        path = Path(self.jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "kind": kind,
            "key": key,
            "status": status,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_post_payload(
    *,
    run_date: str,
    run_id: str,
    sections: dict[str, list[dict[str, str]]],
    chunk_index: int = 1,
    chunk_total: int = 1,
) -> dict[str, object]:
    title = f"Daily Papers {run_date} (Run: {run_id})"
    if chunk_total > 1:
        title = f"{title} [{chunk_index}/{chunk_total}]"

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "sections": sections,
                }
            }
        },
    }


def flatten_section_entries(sections: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for section_name in ["For You", "Trending Now"]:
        for entry in sections.get(section_name, []):
            row = dict(entry)
            row["section"] = section_name
            result.append(row)
    return result


def chunk_entries_by_limits(
    entries: list[dict[str, str]],
    *,
    max_entries: int = 8,
    max_chars: int = 18000,
) -> list[list[dict[str, str]]]:
    if not entries:
        return []

    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0

    for entry in entries:
        entry_chars = _entry_size(entry)
        exceeds_entry_limit = len(current) >= max_entries
        exceeds_char_limit = current_chars + entry_chars > max_chars

        if current and (exceeds_entry_limit or exceeds_char_limit):
            chunks.append(current)
            current = []
            current_chars = 0

        current.append(entry)
        current_chars += entry_chars

    if current:
        chunks.append(current)

    return chunks


def run_fingerprint(run_date: str, webhook_host: str, selected_paper_ids: list[str]) -> str:
    seed = run_date + webhook_host + "|".join(sorted(selected_paper_ids))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def chunk_key(run_fp: str, chunk_index: int, chunk_count: int) -> str:
    seed = f"{run_fp}:{chunk_index}:{chunk_count}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def send_chunks_with_retry(
    *,
    run_id: str,
    run_date: str,
    webhook_host: str,
    selected_paper_ids: list[str],
    chunks: list[list[dict[str, str]]],
    send_fn: Callable[[list[dict[str, str]]], int],
    store: IdempotencyStore,
) -> dict[str, object]:
    """Send chunked payload with run-level then chunk-level idempotency semantics."""
    run_fp = run_fingerprint(run_date, webhook_host, selected_paper_ids)

    if store.run_status.get(run_fp) == "SENT_COMPLETE":
        return {
            "run_status": RUN_SUCCESS,
            "skipped_all": True,
            "events": [],
            "run_id": run_id,
            "push_attempts": 0,
        }

    events: list[dict[str, object]] = []
    chunk_total = len(chunks)
    delivery_failed = False
    push_attempts = 0

    for idx, chunk in enumerate(chunks):
        ck = chunk_key(run_fp, idx, chunk_total)
        if store.chunk_status.get(ck) == "SENT":
            events.append({"chunk": idx, "status": "SKIPPED_ALREADY_SENT", "run_id": run_id})
            continue

        status_code, attempts_used = _attempt_send(send_fn, chunk)
        push_attempts += attempts_used

        if status_code == 200:
            store.set_chunk_status(ck, "SENT")
            events.append(
                {
                    "chunk": idx,
                    "status": "SENT",
                    "status_code": status_code,
                    "run_id": run_id,
                    "attempts": attempts_used,
                }
            )
            continue

        delivery_failed = True
        store.set_chunk_status(ck, "FAILED_PERMANENT")
        events.append(
            {
                "chunk": idx,
                "status": "FAILED_PERMANENT",
                "status_code": status_code,
                "error_code": f"HTTP_{status_code}",
                "run_id": run_id,
                "attempts": attempts_used,
            }
        )

    if delivery_failed:
        store.set_run_status(run_fp, RUN_FAILED_DELIVERY)
        return {
            "run_status": RUN_FAILED_DELIVERY,
            "skipped_all": False,
            "events": events,
            "run_id": run_id,
            "push_attempts": push_attempts,
        }

    store.set_run_status(run_fp, "SENT_COMPLETE")
    return {
        "run_status": RUN_SUCCESS,
        "skipped_all": False,
        "events": events,
        "run_id": run_id,
        "push_attempts": push_attempts,
    }


def _attempt_send(
    send_fn: Callable[[list[dict[str, str]]], int],
    chunk: list[dict[str, str]],
) -> tuple[int, int]:
    max_attempts = 4  # first try + 3 retries
    last_status = 500
    attempts = 0

    while attempts < max_attempts:
        attempts += 1
        status = int(send_fn(chunk))
        last_status = status
        if status == 200:
            return 200, attempts
        if status in NON_RETRYABLE_CODES:
            return status, attempts
        if status not in RETRYABLE_CODES:
            return status, attempts

    return last_status, attempts


def _entry_size(entry: dict[str, str]) -> int:
    text = "|".join(
        [
            str(entry.get("title_en", "")),
            str(entry.get("summary_zh", "")),
            str(entry.get("source_tag", "")),
            str(entry.get("source_url", "")),
        ]
    )
    return len(text)


def webhook_host_from_url(webhook_url: str) -> str:
    text = str(webhook_url).strip()
    if not text:
        return "open.feishu.local"

    parsed = urllib.parse.urlparse(text)
    if parsed.netloc:
        return parsed.netloc
    return "open.feishu.local"


def make_feishu_webhook_sender(
    *,
    webhook_url: str,
    run_id: str,
    run_date: str,
    timeout_seconds: int = 15,
) -> Callable[[list[dict[str, str]]], int]:
    """Create a sender function for send_chunks_with_retry."""
    url = webhook_url.strip()
    timeout = max(3, int(timeout_seconds))

    def _send(chunk: list[dict[str, str]]) -> int:
        text_content = format_feishu_text_chunk(run_date=run_date, run_id=run_id, chunk=chunk)
        payload = {
            "msg_type": "text",
            "content": {"text": text_content},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(response.getcode() or 500)
        except urllib.error.HTTPError as exc:
            return int(exc.code or 500)
        except Exception:
            return 503

        if status != 200:
            return status

        app_code = _extract_feishu_app_code(body)
        if app_code == 0:
            return 200
        if app_code == 429:
            return 429
        return 422

    return _send


def format_feishu_text_chunk(
    *,
    run_date: str,
    run_id: str,
    chunk: list[dict[str, str]],
) -> str:
    lines: list[str] = [f"Daily Papers {run_date} (Run: {run_id})"]
    section_last = ""

    for index, entry in enumerate(chunk, start=1):
        section = str(entry.get("section", "")).strip() or "Papers"
        if section != section_last:
            lines.append("")
            lines.append(f"[{section}]")
            section_last = section

        title = str(entry.get("title_en", "")).strip() or "Untitled"
        source_tag = str(entry.get("source_tag", "")).strip()
        source_url = str(entry.get("source_url", "")).strip()
        summary = str(entry.get("summary_zh", "")).strip()
        summary = _clip_text(summary, 1200)

        lines.append(f"{index}. {title}")
        if source_tag:
            lines.append(f"Source: {source_tag}")
        if source_url:
            lines.append(f"Link: {source_url}")
        if summary:
            lines.append(f"Summary: {summary}")

    return "\n".join(lines).strip()


def _clip_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _extract_feishu_app_code(body: str) -> int:
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return 422

    code_value = obj.get("code", obj.get("StatusCode", 0))
    try:
        code_int = int(code_value)
    except (TypeError, ValueError):
        code_int = 422

    if code_int == 0:
        return 0
    if _looks_like_rate_limit(obj):
        return 429
    return 422


def _looks_like_rate_limit(body_obj: dict[str, object]) -> bool:
    message_fields = [
        str(body_obj.get("msg", "")),
        str(body_obj.get("StatusMessage", "")),
        str(body_obj.get("message", "")),
    ]
    message = " ".join(message_fields).lower()
    return (
        "rate" in message and "limit" in message
    ) or "frequency" in message or "too many requests" in message or "频" in message
