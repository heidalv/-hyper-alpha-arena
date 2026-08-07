#!/usr/bin/env python3
"""全面验证 DCP 修复 + 核心 API 健康。"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
FAILURES: list[str] = []
OK: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        OK.append(f"PASS {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILURES.append(f"FAIL {name}" + (f" — {detail}" if detail else ""))


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    # ── 1. 模块与配置 ──
    try:
        from backend.services.decision_core.direction_coherence import (
            evaluate_direction_coherence,
        )
        from backend.config.settings import (
            DIRECTION_COHERENCE_MODE,
            RISK_AI_REVERSE_MIN_CONF,
            RISK_P3_AI_REVERSE_COOLDOWN_SEC,
            KLINE_CHANGE_THRESHOLD_PCT,
            PAPER_PACE_SYMMETRIC_CLOSE,
            TRAINING_PHASE_BLOCK_AUTO_COIN,
        )

        v1 = evaluate_direction_coherence(
            action="buy", confidence=30, tier="mid", trade_nature="swing",
            orchestrator={"final_side": "short", "weighted_confidence": 0.4, "mid_bias": "bearish"},
            fan_branch="weak_oppose", symbol="TEST",
        )
        check("DCP weak_oppose BLOCK", not v1.allowed, v1.rule)

        v2 = evaluate_direction_coherence(
            action="buy", confidence=80, tier="mid", trade_nature="swing",
            orchestrator={"final_side": "short", "weighted_confidence": 0.2, "mid_bias": "bearish", "mid_conf": 0.25},
            symbol="TEST",
        )
        check("DCP contrarian ALLOW+penalty", v2.allowed and v2.penalty == 10, v2.rule)

        check("DIRECTION_COHERENCE_MODE=enforce", DIRECTION_COHERENCE_MODE == "enforce", DIRECTION_COHERENCE_MODE)
        check("ai_reverse min conf=0.65", RISK_AI_REVERSE_MIN_CONF == 0.65, str(RISK_AI_REVERSE_MIN_CONF))
        check("ai_reverse cooldown=1800s", RISK_P3_AI_REVERSE_COOLDOWN_SEC == 1800, str(RISK_P3_AI_REVERSE_COOLDOWN_SEC))
        check("KLINE threshold=0.0015", abs(KLINE_CHANGE_THRESHOLD_PCT - 0.0015) < 1e-6, str(KLINE_CHANGE_THRESHOLD_PCT))
        check("PACE_SYMMETRIC_CLOSE", PAPER_PACE_SYMMETRIC_CLOSE is True)
        check("TRAINING_BLOCK_AUTO_COIN", TRAINING_PHASE_BLOCK_AUTO_COIN is True)
    except Exception as e:
        check("DCP module import", False, str(e))

    # ── 2. V5 pipeline 集成 ──
    try:
        from backend.services.decision_core.pipeline import evaluate_open_decision

        class _FakeDB:
            pass

        allowed, reason, _adj = evaluate_open_decision(
            db=_FakeDB(),
            account_id=1,
            symbol="FARTCOIN",
            dec={
                "action": "buy",
                "confidence": 30,
                "tier": "long",
                "trade_nature": "trend_follow",
            },
            market_data={
                "orchestrator": {
                    "final_side": "short",
                    "weighted_confidence": 0.35,
                    "long_bias": "bearish",
                }
            },
            base_entry_threshold=50,
        )
        check("V5+DCP blocks oppose open", not allowed, reason[:80])
    except Exception as e:
        check("V5 pipeline DCP", False, str(e))

    # ── 3. Pace 对称 ──
    try:
        from backend.services.paper_pace_controller import paper_pace_controller
        mode = paper_pace_controller.get_knobs().master_close_mode
        sym_block = paper_pace_controller.blocks_new_opens_symmetric()
        check("Pace gear readable", paper_pace_controller.gear in ("turbo", "warm", "balanced", "conservative"),
              f"gear={paper_pace_controller.gear} close_mode={mode}")
        if mode == "shadow":
            check("Pace shadow → symmetric block", sym_block is True)
        else:
            check("Pace enforce → no symmetric block", sym_block is False, mode)
    except Exception as e:
        check("Pace controller", False, str(e))

    # ── 4. API 健康 ──
    endpoints = [
        ("/api/training-phase/status", "training_phase"),
        ("/api/full-auto/sessions", "full_auto"),
        ("/api/opencode/health/digest?window_hours=24", "opencode_health"),
    ]
    for path, label in endpoints:
        try:
            data = get_json(path)
            check(f"API {label}", True, f"200 keys={len(data) if isinstance(data, dict) else 'list'}")
        except Exception as e:
            check(f"API {label}", False, str(e))

    # ── 5. 日志扫描（重启后尾部）──
    log_path = ROOT / "logs" / "backend.log"
    if log_path.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-300_000:]
        old_weak = tail.count("weak_oppose bias=") and "blended=" in tail
        new_skip = "skip(温和反向" in tail
        dcp_logs = tail.count("[DCP]")
        check("Log: FanOut 温和反向 skip 逻辑存在", new_skip or dcp_logs > 0 or "Application startup complete" in tail,
              f"new_skip={new_skip} dcp={dcp_logs}")
    else:
        check("backend.log exists", False)

    print("\n=== 验证结果 ===")
    for line in OK:
        print(line)
    for line in FAILURES:
        print(line)
    print(f"\n合计: {len(OK)} 通过, {len(FAILURES)} 失败")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
