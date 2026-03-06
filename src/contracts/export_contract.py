"""Export payload contract validation for future Obsidian sync."""

from __future__ import annotations

from datetime import datetime


def validate_export_payload(payload: dict[str, object]) -> tuple[bool, list[str]]:
    errors: list[str] = []

    required_top = ["run_id", "run_date", "timezone", "sections", "metrics", "version"]
    for key in required_top:
        if key not in payload:
            errors.append(f"missing:{key}")

    run_date = payload.get("run_date")
    if isinstance(run_date, str):
        try:
            datetime.strptime(run_date, "%Y-%m-%d")
        except ValueError:
            errors.append("run_date:invalid_format")

    timezone = payload.get("timezone")
    if timezone != "Asia/Shanghai":
        errors.append("timezone:invalid")

    sections = payload.get("sections", [])
    allowed_section_names = {"For You", "Trending Now"}
    seen_names: set[str] = set()

    if not isinstance(sections, list):
        errors.append("sections:not_list")
    else:
        for idx, section in enumerate(sections):
            if not isinstance(section, dict):
                errors.append(f"sections[{idx}]:not_dict")
                continue
            name = section.get("name")
            if not isinstance(name, str):
                errors.append(f"sections[{idx}]:missing_name")
            else:
                seen_names.add(name)
                if name not in allowed_section_names:
                    errors.append(f"sections[{idx}]:invalid_name")
            if "items" not in section or not isinstance(section["items"], list):
                errors.append(f"sections[{idx}]:invalid_items")

    if not allowed_section_names.issubset(seen_names):
        errors.append("sections:missing_required_names")

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics:not_dict")
    else:
        required_metrics = ["fetched_counts_by_source", "dedup_count", "selected_count", "delivery_status"]
        for key in required_metrics:
            if key not in metrics:
                errors.append(f"metrics:missing_{key}")

        source_counts = metrics.get("fetched_counts_by_source")
        if not isinstance(source_counts, dict):
            errors.append("metrics:fetched_counts_by_source_not_dict")
        else:
            for src_key in ["arXiv", "HF Daily"]:
                if src_key not in source_counts:
                    errors.append(f"metrics:fetched_counts_missing_{src_key}")

        if metrics.get("delivery_status") not in {"SUCCESS", "FAILED_SOURCE", "FAILED_DELIVERY"}:
            errors.append("metrics:invalid_delivery_status")

    return (len(errors) == 0, errors)
