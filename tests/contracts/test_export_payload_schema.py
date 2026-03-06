import json
from pathlib import Path

from src.contracts.export_contract import validate_export_payload


def test_export_payload_schema() -> None:
    valid = json.loads(Path("tests/fixtures/contracts/export_payload_valid.json").read_text(encoding="utf-8"))
    invalid = json.loads(Path("tests/fixtures/contracts/export_payload_invalid.json").read_text(encoding="utf-8"))

    ok_valid, errs_valid = validate_export_payload(valid)
    ok_invalid, errs_invalid = validate_export_payload(invalid)

    assert ok_valid is True
    assert errs_valid == []

    assert ok_invalid is False
    assert len(errs_invalid) >= 1
