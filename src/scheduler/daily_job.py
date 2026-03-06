"""Daily scheduler orchestration for the MVP pipeline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, TypeVar
from zoneinfo import ZoneInfo

from src.connectors.arxiv_connector import (
    fetch_arxiv_by_date,
    fetch_arxiv_by_window,
    fetch_arxiv_latest,
    parse_arxiv_feed,
)
from src.connectors.hf_papers_connector import fetch_hf_daily_by_date, parse_hf_daily_payload
from src.delivery.feishu_adapter import (
    IdempotencyStore,
    chunk_entries_by_limits,
    flatten_section_entries,
    make_feishu_webhook_sender,
    send_chunks_with_retry,
    webhook_host_from_url,
)
from src.models import RUN_FAILED_DELIVERY, RUN_FAILED_SOURCE, RUN_SKIPPED_LOCKED, RUN_SUCCESS
from src.pipeline.dedup import deduplicate_papers
from src.pipeline.normalize import normalize_records
from src.ranking.scoring import score_papers
from src.ranking.selection import select_dual_track
from src.summarization.summarizer import summarize_papers


T = TypeVar("T")
PIPELINE_STEP_RETRY_SECONDS = [60, 180, 600]
DEFAULT_SCHEDULER_STATE_PATH = "data/state/scheduler_state.json"
DEFAULT_CONFIG_PATH = "config/default.yaml"
LOCAL_CONFIG_PATH = "config/local.yaml"


@dataclass
class RunResult:
    run_status: str
    fetched_counts_by_source: dict[str, int]
    dedup_count: int
    selected_count: int
    sections: dict[str, list[dict[str, object]]]
    push_attempts: int = 0
    delivery_events: list[dict[str, object]] = field(default_factory=list)


def run_once(
    *,
    arxiv_xml: str,
    hf_json: str,
    keywords: list[str],
    source_success_mode: str = "strict_both",
    ranking_weights: dict[str, float] | None = None,
    for_you_n: int = 5,
    trending_n: int = 5,
    max_msg_chars: int = 18000,
    max_entries_per_chunk: int = 8,
    run_id: str = "manual-run",
    run_date: str = "1970-01-01",
    webhook_host: str = "open.feishu.local",
    send_fn: Callable[[list[dict[str, str]]], int] | None = None,
    idempotency_store: IdempotencyStore | None = None,
    lock_path: str = "data/state/run.lock",
    step_retry_sleep_fn: Callable[[float], None] | None = None,
    use_llm_summary: bool = True,
    summary_language: str = "zh-CN",
    summary_mode: str = "strict",
    summary_enhanced_max_chars: int = 8000,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
) -> RunResult:
    """Run one full pipeline cycle using provided payloads (test-friendly)."""
    lock_fd = _acquire_lock(lock_path)
    if lock_fd is None:
        return RunResult(
            run_status=RUN_SKIPPED_LOCKED,
            fetched_counts_by_source={"arXiv": 0, "HF Daily": 0},
            dedup_count=0,
            selected_count=0,
            sections={"For You": [], "Trending Now": []},
            push_attempts=0,
            delivery_events=[],
        )

    try:
        raw_arxiv = with_pipeline_step_retry(
            lambda: parse_arxiv_feed(arxiv_xml),
            [],
            sleep_fn=step_retry_sleep_fn,
        )
        raw_hf = with_pipeline_step_retry(
            lambda: parse_hf_daily_payload(hf_json),
            [],
            sleep_fn=step_retry_sleep_fn,
        )

        return _execute_pipeline(
            raw_arxiv=raw_arxiv,
            raw_hf=raw_hf,
            keywords=keywords,
            source_success_mode=source_success_mode,
            ranking_weights=ranking_weights,
            for_you_n=for_you_n,
            trending_n=trending_n,
            max_msg_chars=max_msg_chars,
            max_entries_per_chunk=max_entries_per_chunk,
            run_id=run_id,
            run_date=run_date,
            webhook_host=webhook_host,
            send_fn=send_fn,
            idempotency_store=idempotency_store,
            step_retry_sleep_fn=step_retry_sleep_fn,
            use_llm_summary=use_llm_summary,
            summary_language=summary_language,
            summary_mode=summary_mode,
            summary_enhanced_max_chars=summary_enhanced_max_chars,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )
    finally:
        _release_lock(lock_fd, lock_path)


def run_live_for_date(
    *,
    target_date: date,
    keywords: list[str],
    arxiv_categories: list[str],
    arxiv_focus_terms: list[str] | None = None,
    arxiv_focus_mode: str = "any",
    max_results_per_source: int = 50,
    hf_fallback_days: int = 3,
    source_success_mode: str = "strict_both",
    ranking_weights: dict[str, float] | None = None,
    for_you_n: int = 5,
    trending_n: int = 5,
    max_msg_chars: int = 18000,
    max_entries_per_chunk: int = 8,
    run_id: str = "manual-run",
    webhook_host: str = "open.feishu.local",
    send_fn: Callable[[list[dict[str, str]]], int] | None = None,
    idempotency_store: IdempotencyStore | None = None,
    lock_path: str = "data/state/run.lock",
    step_retry_sleep_fn: Callable[[float], None] | None = None,
    use_llm_summary: bool = True,
    summary_language: str = "zh-CN",
    summary_mode: str = "strict",
    summary_enhanced_max_chars: int = 8000,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    arxiv_window_start_utc: datetime | None = None,
    arxiv_window_end_utc: datetime | None = None,
    previous_sent_paper_ids: list[str] | None = None,
) -> RunResult:
    """Run one full cycle with real online sources for the given date."""
    lock_fd = _acquire_lock(lock_path)
    if lock_fd is None:
        return RunResult(
            run_status=RUN_SKIPPED_LOCKED,
            fetched_counts_by_source={"arXiv": 0, "HF Daily": 0},
            dedup_count=0,
            selected_count=0,
            sections={"For You": [], "Trending Now": []},
            push_attempts=0,
            delivery_events=[],
        )

    try:
        if arxiv_window_start_utc is not None and arxiv_window_end_utc is not None:
            raw_arxiv = with_pipeline_step_retry(
                lambda: fetch_arxiv_by_window(
                    start_datetime=arxiv_window_start_utc,
                    end_datetime=arxiv_window_end_utc,
                    categories=arxiv_categories,
                    focus_terms=arxiv_focus_terms,
                    focus_mode=arxiv_focus_mode,
                    max_results=max_results_per_source,
                ),
                [],
                sleep_fn=step_retry_sleep_fn,
            )
        else:
            raw_arxiv = with_pipeline_step_retry(
                lambda: fetch_arxiv_by_date(
                    target_date=target_date,
                    categories=arxiv_categories,
                    focus_terms=arxiv_focus_terms,
                    focus_mode=arxiv_focus_mode,
                    max_results=max_results_per_source,
                ),
                [],
                sleep_fn=step_retry_sleep_fn,
            )
        if not raw_arxiv:
            raw_arxiv = with_pipeline_step_retry(
                lambda: fetch_arxiv_latest(
                    categories=arxiv_categories,
                    focus_terms=arxiv_focus_terms,
                    focus_mode=arxiv_focus_mode,
                    max_results=max_results_per_source,
                ),
                [],
                sleep_fn=step_retry_sleep_fn,
            )

        raw_hf = with_pipeline_step_retry(
            lambda: fetch_hf_daily_by_date(
                target_date=target_date,
                max_results=max_results_per_source,
                fallback_days=hf_fallback_days,
            ),
            [],
            sleep_fn=step_retry_sleep_fn,
        )

        return _execute_pipeline(
            raw_arxiv=raw_arxiv,
            raw_hf=raw_hf,
            keywords=keywords,
            source_success_mode=source_success_mode,
            ranking_weights=ranking_weights,
            for_you_n=for_you_n,
            trending_n=trending_n,
            max_msg_chars=max_msg_chars,
            max_entries_per_chunk=max_entries_per_chunk,
            run_id=run_id,
            run_date=target_date.isoformat(),
            webhook_host=webhook_host,
            send_fn=send_fn,
            idempotency_store=idempotency_store,
            step_retry_sleep_fn=step_retry_sleep_fn,
            use_llm_summary=use_llm_summary,
            summary_language=summary_language,
            summary_mode=summary_mode,
            summary_enhanced_max_chars=summary_enhanced_max_chars,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            previous_sent_paper_ids=previous_sent_paper_ids,
        )
    finally:
        _release_lock(lock_fd, lock_path)


def _execute_pipeline(
    *,
    raw_arxiv: list[dict[str, object]],
    raw_hf: list[dict[str, object]],
    keywords: list[str],
    source_success_mode: str,
    ranking_weights: dict[str, float] | None,
    for_you_n: int,
    trending_n: int,
    max_msg_chars: int,
    max_entries_per_chunk: int,
    run_id: str,
    run_date: str,
    webhook_host: str,
    send_fn: Callable[[list[dict[str, str]]], int] | None,
    idempotency_store: IdempotencyStore | None,
    step_retry_sleep_fn: Callable[[float], None] | None,
    use_llm_summary: bool,
    summary_language: str,
    summary_mode: str,
    summary_enhanced_max_chars: int,
    llm_api_key: str | None,
    llm_base_url: str | None,
    llm_model: str | None,
    previous_sent_paper_ids: list[str] | None = None,
) -> RunResult:
    fetched_counts = {"arXiv": len(raw_arxiv), "HF Daily": len(raw_hf)}

    source_gate_passed = True
    if source_success_mode == "strict_both":
        source_gate_passed = len(raw_arxiv) > 0 and len(raw_hf) > 0
    elif source_success_mode == "partial_any":
        source_gate_passed = len(raw_arxiv) > 0 or len(raw_hf) > 0

    if not source_gate_passed:
        return RunResult(
            run_status=RUN_FAILED_SOURCE,
            fetched_counts_by_source=fetched_counts,
            dedup_count=0,
            selected_count=0,
            sections={"For You": [], "Trending Now": []},
            push_attempts=0,
            delivery_events=[],
        )

    raw_all = raw_arxiv + raw_hf
    normalized = normalize_records(raw_all)
    deduped, _audits = deduplicate_papers(normalized)
    if previous_sent_paper_ids:
        previous_ids = set(previous_sent_paper_ids)
        deduped = [paper for paper in deduped if paper.paper_id not in previous_ids]
    score_papers(deduped, keywords, weights=ranking_weights)

    selected = select_dual_track(deduped, for_you_n=for_you_n, trending_n=trending_n)
    merged = selected["For You"] + selected["Trending Now"]
    summarize_papers(
        merged,
        use_llm=use_llm_summary,
        language=summary_language,
        summary_mode=summary_mode,
        enhanced_max_chars=summary_enhanced_max_chars,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
    )

    section_dict = {
        "For You": [paper.to_dict() for paper in selected["For You"]],
        "Trending Now": [paper.to_dict() for paper in selected["Trending Now"]],
    }

    if send_fn is None:
        send_fn = lambda _chunk: 200

    if idempotency_store is None:
        idempotency_store = IdempotencyStore(
            jsonl_path="data/state/delivery_idempotency.jsonl",
        )

    entries = flatten_section_entries(section_dict)
    chunks = chunk_entries_by_limits(
        entries,
        max_entries=max_entries_per_chunk,
        max_chars=max_msg_chars,
    )

    delivery_result = with_pipeline_step_retry(
        lambda: send_chunks_with_retry(
            run_id=run_id,
            run_date=run_date,
            webhook_host=webhook_host,
            selected_paper_ids=[paper.paper_id for paper in merged],
            chunks=chunks,
            send_fn=send_fn,
            store=idempotency_store,
        ),
        {
            "run_status": RUN_FAILED_DELIVERY,
            "skipped_all": False,
            "events": [],
            "run_id": run_id,
            "push_attempts": 0,
        },
        sleep_fn=step_retry_sleep_fn,
    )

    run_status = delivery_result["run_status"]
    if run_status not in {RUN_SUCCESS, RUN_FAILED_DELIVERY}:
        run_status = RUN_FAILED_DELIVERY

    return RunResult(
        run_status=run_status,
        fetched_counts_by_source=fetched_counts,
        dedup_count=len(raw_all) - len(deduped),
        selected_count=len(merged),
        sections=section_dict,
        push_attempts=int(delivery_result.get("push_attempts", 0)),
        delivery_events=list(delivery_result.get("events", [])),
    )


def simulate_7day_soak(events: list[dict[str, object]]) -> dict[str, object]:
    """Evaluate 7-day run quality constraints for AC1-style checks."""
    total = len(events)
    if total == 0:
        return {"success_rate": 0.0, "all_success": False, "p95_runtime": 0.0, "max_drift": 0.0}

    successes = sum(1 for e in events if str(e.get("status", "")) == RUN_SUCCESS)
    runtimes = sorted(float(e.get("runtime_sec", 0.0)) for e in events)
    drifts = [float(e.get("start_drift_sec", 0.0)) for e in events]

    p95_index = min(len(runtimes) - 1, int(round(0.95 * (len(runtimes) - 1))))
    p95_runtime = runtimes[p95_index]

    return {
        "success_rate": successes / total,
        "all_success": successes == total,
        "p95_runtime": p95_runtime,
        "max_drift": max(drifts) if drifts else 0.0,
    }


def should_run_now(now_utc: datetime, daily_time: str, tz_name: str) -> bool:
    if tz_name != "Asia/Shanghai":
        return False

    hour, minute = [int(x) for x in daily_time.split(":", 1)]
    local_hour = (now_utc.hour + 8) % 24
    local_minute = now_utc.minute
    return local_hour == hour and local_minute == minute


def generate_run_id(now_utc: datetime, repo_root: str = ".") -> str:
    sha_short = "nosha000"
    try:
        output = subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if output:
            sha_short = output
    except Exception:
        pass

    return now_utc.strftime(f"%Y%m%d-%H%M-{sha_short}")


def load_default_config(path: str) -> dict[str, object]:
    """Parse a small subset of YAML used by this project config."""
    text = Path(path).read_text(encoding="utf-8")
    cfg: dict[str, object] = {
        "scheduler": {
            "timezone": "Asia/Shanghai",
            "daily_time": "09:00",
            "state_path": DEFAULT_SCHEDULER_STATE_PATH,
        },
        "source": {
            "source_success_mode": "strict_both",
            "arxiv_categories": "cs.AI,cs.LG,cs.CL,cs.CV",
            "arxiv_focus_terms": "",
            "arxiv_focus_mode": "any",
            "hf_fallback_days": 3,
        },
        "fetch": {"max_results_per_source": 50},
        "delivery": {
            "max_msg_chars": 18000,
            "max_entries_per_chunk": 8,
            "webhook_url": "",
            "webhook_timeout_sec": 15,
        },
        "summary": {
            "use_llm": 1,
            "language": "zh-CN",
            "mode": "strict",
            "enhanced_max_chars": 8000,
            "api_key": "",
            "base_url": "",
            "model": "gpt-4o-mini",
        },
        "ranking": {
            "for_you_n": 5,
            "trending_n": 5,
            "keywords": "transformer,retrieval,diffusion,vision,agent",
            "weights": {"interest": 0.65, "freshness": 0.20, "trending": 0.15},
        },
    }

    current_section: str | None = None
    current_subsection: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.strip().startswith("#"):
            continue

        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            current_subsection = None
            if current_section not in cfg:
                cfg[current_section] = {}
            continue

        if current_section is None or ":" not in line:
            continue

        indent = len(line) - len(line.lstrip(" "))
        key, value = line.strip().split(":", 1)
        value = value.strip().strip('"')

        if indent == 2 and value == "":
            current_subsection = key
            section_dict = cfg.setdefault(current_section, {})
            if isinstance(section_dict, dict):
                section_dict.setdefault(current_subsection, {})
            continue

        target = cfg[current_section]
        if isinstance(target, dict) and current_subsection and indent >= 4:
            sub = target.setdefault(current_subsection, {})
            if isinstance(sub, dict):
                sub[key] = _coerce(value)
        elif isinstance(target, dict):
            target[key] = _coerce(value)

    return cfg


def _coerce(value: str) -> object:
    if value in {"", "null", "None"}:
        return ""
    if value.lower() in {"true", "false"}:
        return 1 if value.lower() == "true" else 0
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def with_pipeline_step_retry(
    fn: Callable[[], T],
    fallback: T,
    *,
    sleep_fn: Callable[[float], None] | None = None,
    retry_delays: list[int] | None = None,
) -> T:
    if sleep_fn is None:
        sleep_fn = time.sleep
    if retry_delays is None:
        retry_delays = PIPELINE_STEP_RETRY_SECONDS

    attempts = 1 + len(retry_delays)
    for idx in range(attempts):
        try:
            return fn()
        except Exception:
            if idx < len(retry_delays):
                sleep_fn(float(retry_delays[idx]))
            continue
    return fallback


def _acquire_lock(lock_path: str) -> int | None:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return fd
    except FileExistsError:
        # Clear stale lock when recorded PID no longer exists.
        try:
            content = path.read_text(encoding="utf-8").strip()
            pid = int(content) if content else -1
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    return None
                except ProcessLookupError:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    return None
            else:
                path.unlink(missing_ok=True)
        except Exception:
            return None

        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            return fd
        except FileExistsError:
            return None


def _release_lock(lock_fd: int | None, lock_path: str) -> None:
    if lock_fd is None:
        return
    try:
        os.close(lock_fd)
    finally:
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        return [x.strip().strip('"').strip("'") for x in text.split(",") if x.strip()]
    return []


def resolve_config_path(requested_path: str | None = None) -> str:
    if requested_path:
        return requested_path
    if Path(LOCAL_CONFIG_PATH).exists():
        return LOCAL_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def load_scheduler_state(path: str = DEFAULT_SCHEDULER_STATE_PATH) -> dict[str, object]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_scheduler_state(
    *,
    pushed_at_utc: datetime,
    selected_paper_ids: list[str],
    run_id: str,
    path: str = DEFAULT_SCHEDULER_STATE_PATH,
) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_success_at_utc": pushed_at_utc.astimezone(UTC).isoformat(),
        "last_run_id": run_id,
        "last_selected_paper_ids": list(selected_paper_ids),
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_arxiv_window(
    *,
    now_utc: datetime,
    daily_time: str,
    tz_name: str,
    scheduler_state: dict[str, object],
) -> tuple[datetime, datetime]:
    last_success_raw = str(scheduler_state.get("last_success_at_utc", "")).strip()
    if last_success_raw:
        try:
            start_utc = datetime.fromisoformat(last_success_raw.replace("Z", "+00:00"))
            if start_utc.tzinfo is None:
                start_utc = start_utc.replace(tzinfo=UTC)
            return start_utc.astimezone(UTC), now_utc.astimezone(UTC)
        except ValueError:
            pass

    return now_utc.astimezone(UTC) - _scheduled_interval(daily_time, tz_name), now_utc.astimezone(UTC)


def _scheduled_interval(daily_time: str, tz_name: str) -> timedelta:
    try:
        hour, minute = [int(x) for x in daily_time.split(":", 1)]
    except ValueError:
        hour, minute = 9, 0

    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = ZoneInfo("Asia/Shanghai")

    base = datetime(2026, 1, 1, hour, minute, tzinfo=tzinfo)
    next_day = base + timedelta(days=1)
    return next_day.astimezone(UTC) - base.astimezone(UTC)


def selected_paper_ids_from_sections(sections: dict[str, list[dict[str, object]]]) -> list[str]:
    result: list[str] = []
    for section_name in ["For You", "Trending Now"]:
        for entry in sections.get(section_name, []):
            paper_id = str(entry.get("paper_id", "")).strip()
            if paper_id:
                result.append(paper_id)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily paper push scheduler")
    parser.add_argument("--config", default="")
    parser.add_argument("--check-schedule", action="store_true")
    parser.add_argument("--run-once-fixtures", action="store_true")
    parser.add_argument("--run-live-today", action="store_true")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config.strip() or None)
    cfg = load_default_config(config_path)
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg, dict) else {}
    source_cfg = cfg.get("source", {}) if isinstance(cfg, dict) else {}
    fetch_cfg = cfg.get("fetch", {}) if isinstance(cfg, dict) else {}
    ranking_cfg = cfg.get("ranking", {}) if isinstance(cfg, dict) else {}
    delivery_cfg = cfg.get("delivery", {}) if isinstance(cfg, dict) else {}
    summary_cfg = cfg.get("summary", {}) if isinstance(cfg, dict) else {}

    now = datetime.now(UTC)
    daily_time = str(scheduler_cfg.get("daily_time", "09:00")) if isinstance(scheduler_cfg, dict) else "09:00"
    tz_name = str(scheduler_cfg.get("timezone", "Asia/Shanghai")) if isinstance(scheduler_cfg, dict) else "Asia/Shanghai"
    scheduler_state_path = (
        str(scheduler_cfg.get("state_path", DEFAULT_SCHEDULER_STATE_PATH))
        if isinstance(scheduler_cfg, dict)
        else DEFAULT_SCHEDULER_STATE_PATH
    )
    default_keywords = ["transformer", "retrieval", "diffusion", "vision", "agent"]
    ranking_keywords = (
        _as_list(ranking_cfg.get("keywords", ",".join(default_keywords)))
        if isinstance(ranking_cfg, dict)
        else default_keywords
    )
    if not ranking_keywords:
        ranking_keywords = default_keywords

    if args.check_schedule:
        due = should_run_now(now, daily_time, tz_name)
        print("DUE" if due else "SKIP")
        return 0

    if args.run_once_fixtures:
        arxiv_xml = Path("tests/fixtures/sources/arxiv_sample_feed.xml").read_text(encoding="utf-8")
        hf_json = Path("tests/fixtures/sources/hf_daily_sample.json").read_text(encoding="utf-8")
        run_id = generate_run_id(now)
        run_date = now.strftime("%Y-%m-%d")
        webhook_url = str(delivery_cfg.get("webhook_url", "")).strip() if isinstance(delivery_cfg, dict) else ""
        webhook_host = webhook_host_from_url(webhook_url)
        webhook_timeout = int(delivery_cfg.get("webhook_timeout_sec", 15)) if isinstance(delivery_cfg, dict) else 15
        send_fn = (
            make_feishu_webhook_sender(
                webhook_url=webhook_url,
                run_id=run_id,
                run_date=run_date,
                timeout_seconds=webhook_timeout,
            )
            if webhook_url
            else None
        )

        result = run_once(
            arxiv_xml=arxiv_xml,
            hf_json=hf_json,
            keywords=ranking_keywords,
            source_success_mode=str(source_cfg.get("source_success_mode", "strict_both")) if isinstance(source_cfg, dict) else "strict_both",
            ranking_weights=dict(ranking_cfg.get("weights", {})) if isinstance(ranking_cfg, dict) and isinstance(ranking_cfg.get("weights", {}), dict) else None,
            for_you_n=int(ranking_cfg.get("for_you_n", 5)) if isinstance(ranking_cfg, dict) else 5,
            trending_n=int(ranking_cfg.get("trending_n", 5)) if isinstance(ranking_cfg, dict) else 5,
            max_msg_chars=int(delivery_cfg.get("max_msg_chars", 18000)) if isinstance(delivery_cfg, dict) else 18000,
            max_entries_per_chunk=int(delivery_cfg.get("max_entries_per_chunk", 8)) if isinstance(delivery_cfg, dict) else 8,
            run_id=run_id,
            run_date=run_date,
            webhook_host=webhook_host,
            send_fn=send_fn,
            lock_path="data/state/run.lock",
            use_llm_summary=bool(int(summary_cfg.get("use_llm", 1))) if isinstance(summary_cfg, dict) else True,
            summary_language=str(summary_cfg.get("language", "zh-CN")) if isinstance(summary_cfg, dict) else "zh-CN",
            summary_mode=str(summary_cfg.get("mode", "strict")) if isinstance(summary_cfg, dict) else "strict",
            summary_enhanced_max_chars=int(summary_cfg.get("enhanced_max_chars", 8000)) if isinstance(summary_cfg, dict) else 8000,
            llm_api_key=str(summary_cfg.get("api_key", "")).strip() if isinstance(summary_cfg, dict) else "",
            llm_base_url=str(summary_cfg.get("base_url", "")).strip() if isinstance(summary_cfg, dict) else "",
            llm_model=str(summary_cfg.get("model", "gpt-4o-mini")).strip() if isinstance(summary_cfg, dict) else "gpt-4o-mini",
        )
        print(json.dumps(result.__dict__, ensure_ascii=False))
        return 0

    if args.run_live_today:
        run_id = generate_run_id(now)
        run_date = now.strftime("%Y-%m-%d")
        scheduler_state = load_scheduler_state(scheduler_state_path)
        arxiv_window_start_utc, arxiv_window_end_utc = resolve_arxiv_window(
            now_utc=now,
            daily_time=daily_time,
            tz_name=tz_name,
            scheduler_state=scheduler_state,
        )
        webhook_url = str(delivery_cfg.get("webhook_url", "")).strip() if isinstance(delivery_cfg, dict) else ""
        webhook_host = webhook_host_from_url(webhook_url)
        webhook_timeout = int(delivery_cfg.get("webhook_timeout_sec", 15)) if isinstance(delivery_cfg, dict) else 15
        send_fn = (
            make_feishu_webhook_sender(
                webhook_url=webhook_url,
                run_id=run_id,
                run_date=run_date,
                timeout_seconds=webhook_timeout,
            )
            if webhook_url
            else None
        )

        result = run_live_for_date(
            target_date=now.date(),
            keywords=ranking_keywords,
            arxiv_categories=_as_list(source_cfg.get("arxiv_categories", "cs.AI,cs.LG,cs.CL,cs.CV")) if isinstance(source_cfg, dict) else ["cs.AI", "cs.LG", "cs.CL", "cs.CV"],
            arxiv_focus_terms=_as_list(source_cfg.get("arxiv_focus_terms", "")) if isinstance(source_cfg, dict) else [],
            arxiv_focus_mode=str(source_cfg.get("arxiv_focus_mode", "any")) if isinstance(source_cfg, dict) else "any",
            max_results_per_source=int(fetch_cfg.get("max_results_per_source", 50)) if isinstance(fetch_cfg, dict) else 50,
            hf_fallback_days=int(source_cfg.get("hf_fallback_days", 3)) if isinstance(source_cfg, dict) else 3,
            source_success_mode=str(source_cfg.get("source_success_mode", "strict_both")) if isinstance(source_cfg, dict) else "strict_both",
            ranking_weights=dict(ranking_cfg.get("weights", {})) if isinstance(ranking_cfg, dict) and isinstance(ranking_cfg.get("weights", {}), dict) else None,
            for_you_n=int(ranking_cfg.get("for_you_n", 5)) if isinstance(ranking_cfg, dict) else 5,
            trending_n=int(ranking_cfg.get("trending_n", 5)) if isinstance(ranking_cfg, dict) else 5,
            max_msg_chars=int(delivery_cfg.get("max_msg_chars", 18000)) if isinstance(delivery_cfg, dict) else 18000,
            max_entries_per_chunk=int(delivery_cfg.get("max_entries_per_chunk", 8)) if isinstance(delivery_cfg, dict) else 8,
            run_id=run_id,
            webhook_host=webhook_host,
            send_fn=send_fn,
            lock_path="data/state/run.lock",
            use_llm_summary=bool(int(summary_cfg.get("use_llm", 1))) if isinstance(summary_cfg, dict) else True,
            summary_language=str(summary_cfg.get("language", "zh-CN")) if isinstance(summary_cfg, dict) else "zh-CN",
            summary_mode=str(summary_cfg.get("mode", "strict")) if isinstance(summary_cfg, dict) else "strict",
            summary_enhanced_max_chars=int(summary_cfg.get("enhanced_max_chars", 8000)) if isinstance(summary_cfg, dict) else 8000,
            llm_api_key=str(summary_cfg.get("api_key", "")).strip() if isinstance(summary_cfg, dict) else "",
            llm_base_url=str(summary_cfg.get("base_url", "")).strip() if isinstance(summary_cfg, dict) else "",
            llm_model=str(summary_cfg.get("model", "gpt-4o-mini")).strip() if isinstance(summary_cfg, dict) else "gpt-4o-mini",
            arxiv_window_start_utc=arxiv_window_start_utc,
            arxiv_window_end_utc=arxiv_window_end_utc,
            previous_sent_paper_ids=list(scheduler_state.get("last_selected_paper_ids", []))
            if isinstance(scheduler_state.get("last_selected_paper_ids", []), list)
            else [],
        )
        if result.run_status == RUN_SUCCESS:
            save_scheduler_state(
                pushed_at_utc=now,
                selected_paper_ids=selected_paper_ids_from_sections(result.sections),
                run_id=run_id,
                path=scheduler_state_path,
            )
        print(json.dumps(result.__dict__, ensure_ascii=False))
        return 0

    print(f"scheduler ready: daily {daily_time} {tz_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
