# -*- coding: utf-8 -*-
"""返佣 / 套利配置快照（rebate_arb + arb 双链路，供看板与实验卡引用）。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "cashflow"


def snapshot_rebate_config() -> Dict[str, Any]:
    from backend.services.rebate_arb.arb_switches import get_arb_switch_status

    payload: Dict[str, Any] = {"ts_ms": int(time.time() * 1000), "switches": {}}
    try:
        payload["switches"] = get_arb_switch_status().to_dict()
    except Exception as exc:
        payload["switches_error"] = str(exc)[:200]

    try:
        from backend.config.rebate_config_loader import get_rebate_config

        rc = get_rebate_config() or {}
        eng = rc.get("engine") if isinstance(rc, dict) else {}
        payload["rebate_engine"] = {
            "paper_mode": eng.get("paper_mode"),
            "auto_execute": eng.get("auto_execute"),
            "max_open_positions": eng.get("max_open_positions"),
        }
    except Exception as exc:
        payload["rebate_config_error"] = str(exc)[:200]

    try:
        from backend.config.arb_config_loader import get_arb_config

        ac = get_arb_config() or {}
        payload["arb_config"] = {
            "funding_arb_enabled": ac.get("funding_arb_enabled"),
            "basis_scan_enabled": ac.get("basis_scan_enabled"),
            "min_spread_bps": ac.get("min_spread_bps"),
        }
    except Exception as exc:
        payload["arb_config_error"] = str(exc)[:200]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "rebate_config_snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    payload["path"] = str(path)
    return payload
