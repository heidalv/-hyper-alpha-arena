"""Mid/Long Agent 链路验收 — Fix18 → Execute → Envelope → place_order。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def main():
    print("=== verify_midlong_chain ===\n")

    try:
        from backend.services.position_exit_state import merge_exit_state
        merged = merge_exit_state(
            {"trend_adjustment": {"trailing_atr_mult": 2.5}},
            {"nature_staged_tp": {"peak_pnl_pct": 1.2}},
        )
        check("merge_exit_state 保留 trend_adjustment", "trend_adjustment" in merged)
        check("merge_exit_state 写入 staged_tp", "nature_staged_tp" in merged)
    except Exception as exc:
        check("merge_exit_state", False, str(exc))

    try:
        from backend.services.agent_decision_envelope import AgentDecisionEnvelope
        env = AgentDecisionEnvelope.new("swing_agent", alignment_score=8)
        dec = {}
        env.attach_to_dec(dec)
        check("AgentDecisionEnvelope attach", dec.get("_agent_envelope", {}).get("agent_source") == "swing_agent")
    except Exception as exc:
        check("AgentDecisionEnvelope", False, str(exc))

    try:
        from backend.services.mid_long_quant_brief import mid_long_quant_brief_builder
        brief = mid_long_quant_brief_builder.build(
            "BTC",
            {"indicators_1h": {"rsi": 55, "ema_trend": "bullish", "macd_hist": 0.1, "vol_ratio": 1.2}},
            {"mid_bias": "bullish", "mid_confidence": 0.5, "long_bias": "bullish", "long_confidence": 0.6},
            "long",
        )
        check("MidLongQuantBrief alignment", brief.alignment_score >= 4)
    except Exception as exc:
        check("MidLongQuantBrief", False, str(exc))

    try:
        from backend.services.full_auto_trading_service import FullAutoTradingService
        svc = FullAutoTradingService()
        payload = svc._orch_payload_from_decision(type("D", (), {
            "final_action": "enter",
            "final_side": "long",
            "final_position_pct": 0.1,
            "final_leverage": 3,
            "final_sl_pct": 0.03,
            "final_tp_pct": 0.08,
            "allowed_direction": "long",
            "long_view": type("V", (), {"bias": "bullish", "confidence": 0.6})(),
            "mid_view": type("V", (), {"bias": "bullish", "confidence": 0.5})(),
            "short_view": type("V", (), {"bias": "neutral", "confidence": 0.2})(),
            "recommended_slots": ["mid", "long"],
            "slot_actions": {"mid": "create", "long": "create"},
            "slot_reasoning": {"mid": "test"},
            "recommended_nature": "swing",
            "reasoning": "test",
        })())
        check("OrchestratorSlotSnapshot 含 slot_actions", "slot_actions" in payload)
        stubs = svc._inject_orch_scheduled_stubs([], {"BTC": {}})
        check("_inject_orch_scheduled_stubs 可调用", isinstance(stubs, list))
    except Exception as exc:
        check("FullAuto orch helpers", False, str(exc))

    try:
        tp, sl, src = FullAutoTradingService._compute_initial_tp_sl_prices(
            tier="mid",
            action="buy",
            ref_price=100.0,
            atr_pct=0.02,
            sym="BTC",
            dec={
                "_agent_independent": True,
                "_agent_envelope": {
                    "agent_source": "swing_agent",
                    "structure_sl_price": 96.0,
                    "structure_tp_price": 108.0,
                    "sl_pct": 0.04,
                    "tp_pct": 0.08,
                    "sl_source": "agent_structure_sl",
                },
            },
        )
        check("Agent SL 优先 execute", abs(sl - 96.0) < 0.01, f"sl={sl} src={src}")
    except Exception as exc:
        check("Agent SL execute 同源", False, str(exc))

    try:
        from backend.config.settings import (
            MIDLONG_ORCH_SNAPSHOT_V2,
            MIDLONG_AGENT_SL_TO_EXECUTE,
            MIDLONG_QUANT_BRIEF_ENABLED,
        )
        check("Feature flags 可读", all(isinstance(x, bool) for x in (
            MIDLONG_ORCH_SNAPSHOT_V2, MIDLONG_AGENT_SL_TO_EXECUTE, MIDLONG_QUANT_BRIEF_ENABLED,
        )))
    except Exception as exc:
        check("Feature flags", False, str(exc))

    try:
        from backend.services.hermes_prompt_optimizer_engine import OPTIMIZABLE_TASKS
        check("Hermes task_trend_agent_review", "task_trend_agent_review" in OPTIMIZABLE_TASKS)
    except Exception as exc:
        check("Hermes L2 tasks", False, str(exc))

    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
