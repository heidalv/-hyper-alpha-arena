#!/usr/bin/env python3
"""MLTO 运行时验证：backend 健康 + API + prompt/slices 辅助函数。"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

API_ROOT = os.environ.get("MLTO_VERIFY_API", "http://127.0.0.1:8000")
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=== verify_mlto_runtime ===\n")

    try:
        with urllib.request.urlopen(f"{API_ROOT}/api/health", timeout=10) as r:
            health = json.loads(r.read().decode())
        check("backend /api/health", health.get("status") == "healthy", str(health.get("status")))
    except Exception as exc:
        check("backend /api/health", False, str(exc))

    try:
        with urllib.request.urlopen(f"{API_ROOT}/openapi.json", timeout=15) as r:
            spec = json.loads(r.read().decode())
        paths = spec.get("paths", {})
        check(
            "openapi mlto thesis/summary",
            any("/api/mlto/sessions/" in p and "thesis/summary" in p for p in paths),
        )
    except Exception as exc:
        check("openapi mlto routes", False, str(exc))

    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession

    db = SessionLocal()
    try:
        sess = db.query(FullAutoSession).order_by(FullAutoSession.updated_at.desc()).first()
        sid = getattr(sess, "session_id", None) or getattr(sess, "id", None)
        check("FullAuto session in DB", bool(sess), f"session_id={sid}")
        if sid:
            with urllib.request.urlopen(
                f"{API_ROOT}/api/mlto/sessions/{sid}/thesis/summary", timeout=15
            ) as r:
                summary = json.loads(r.read().decode())
            n = len(summary.get("items") or summary.get("theses") or [])
            check("GET thesis/summary", isinstance(summary, dict), f"items={n}")

            with urllib.request.urlopen(
                f"{API_ROOT}/api/mlto/sessions/{sid}/thesis?symbol=BTCUSDT&tier=mid",
                timeout=15,
            ) as r:
                detail = json.loads(r.read().decode())
            check(
                "GET thesis detail",
                "thesis" in detail or "gate_status" in detail,
                ",".join(list(detail.keys())[:6]),
            )
    finally:
        db.close()

    from backend.services.trading_analysts import _build_symbol_tier_slices, merge_reports_with_tier_slices

    slices = _build_symbol_tier_slices(
        {"kline": {"signals": [{"symbol": "BTCUSDT", "signal": "bullish", "detail": "t"}]}},
        ["BTCUSDT"],
    )
    merged = merge_reports_with_tier_slices({"reports": {}, "symbol_tier_slices": slices})
    check(
        "symbol_tier_slices",
        "BTCUSDT" in merged.get("_symbol_tier_slices", {}),
        f"mid={len(slices.get('BTCUSDT', {}).get('mid', []))}",
    )

    from backend.services.prompt_registry import get_prompt_registry

    reg = get_prompt_registry()
    for tid in ("task_swing_thesis_update", "task_trend_thesis_update"):
        txt = reg.render_task(
            tid,
            {
                "symbol": "BTCUSDT",
                "thesis_block": "- direction: long",
                "memory_block": "m",
                "delta_block": "d",
                "constraints": "",
            },
        )
        check(f"prompt {tid}", len(txt) > 100)

    print(f"\n合计 PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
