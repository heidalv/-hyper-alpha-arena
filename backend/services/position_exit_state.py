"""position_exit_state — exit_state_json 合并工具（PEO / trend_review / staged_tp 共用）。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def parse_exit_state(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def merge_exit_state(existing: Optional[Dict[str, Any]], patch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """深度合并 exit_state，嵌套 dict 做浅合并，避免 PEO 覆盖 trend_adjustment。"""
    merged = dict(existing or {})
    for key, val in (patch or {}).items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **val}
        else:
            merged[key] = val
    return merged


def dump_exit_state(state: Dict[str, Any]) -> str:
    return json.dumps(state or {}, ensure_ascii=False)
