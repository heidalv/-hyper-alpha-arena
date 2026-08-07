"""训练期审计日志 — training_audit / training_live_audit jsonl（带体积轮转）。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from backend.utils.jsonl_rotating import append_jsonl

TRAINING_AUDIT_FILE = os.path.join("data", "training_audit.jsonl")
LIVE_AUDIT_FILE = os.path.join("data", "training_live_audit.jsonl")
REPORT_DIR = os.path.join("data", "training_reports")


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    rec = dict(record)
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    append_jsonl(path, rec)


def log_training_event(event: str, **fields: Any) -> None:
    _append_jsonl(TRAINING_AUDIT_FILE, {"event": event, **fields})


def log_live_event(event: str, **fields: Any) -> None:
    _append_jsonl(LIVE_AUDIT_FILE, {"event": event, **fields})


def write_graduation_report(strategy_id: str, payload: Dict[str, Any]) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(REPORT_DIR, f"graduation_{strategy_id}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
