"""审计中线/长线设计项是否已接线（静态 grep + 轻量 import）。"""

from __future__ import annotations

import inspect
import json
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


def _read(rel: str) -> str:
    path = os.path.join(ROOT, rel.replace("/", os.sep))
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def main():
    print("=== audit_midlong_activation ===\n")

    # ── settings 默认值 ──
    try:
        from backend.config import settings as s
        check("ORCH_MID_INDEPENDENT_TRIGGER 默认 true", s.ORCH_MID_INDEPENDENT_TRIGGER is True)
        check("MIDLONG_PERSISTENCE_TICKS 默认 2", int(s.MIDLONG_PERSISTENCE_TICKS) >= 2)
        check("UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH 默认 true",
              s.UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH is True)
        check("AGENT_FACT_GUARD_PAPER_ENFORCE 默认 true",
              s.AGENT_FACT_GUARD_PAPER_ENFORCE is True)
    except Exception as exc:
        check("settings flags", False, str(exc))

    # ── 统一执行器 metadata 双路径 ──
    try:
        from backend.services.exchange.executors import OrderContext
        sig = inspect.signature(OrderContext)
        check("OrderContext 含 position_metadata", "position_metadata" in sig.parameters)
        ctx = OrderContext(
            account_id=1, symbol="BTC", side="buy", quantity=0.01, price=100.0,
            position_metadata={"agent_source": "swing_agent"},
        )
        kw = ctx.to_paper_kwargs()
        check("OrderContext.to_paper_kwargs 透传 metadata",
              kw.get("position_metadata", {}).get("agent_source") == "swing_agent")
    except Exception as exc:
        check("OrderContext metadata", False, str(exc))

    fa_src = _read("backend/services/full_auto_trading_service.py")
    check("_execute_paper_trade unified 传 position_metadata",
          "position_metadata=_pos_meta" in fa_src and "_pos_meta = {}" in fa_src)
    check("QAA v3 调用 Fix18",
          "_inject_orch_scheduled_stubs(decisions, market_summary" in fa_src
          and "[Fix18][QAA v3]" in fa_src)
    check("轻量循环用 _orch_payload_from_decision",
          "_orch_payload_from_decision(dec)" in fa_src)
    check("_midlong_persistence_allow 已实现",
          "def _midlong_persistence_allow" in fa_src and "[Persistence]" in fa_src)

    # ── decision_feedback → Agent ──
    try:
        from backend.services.decision_feedback_service import decision_feedback_service
        check("get_agent_constraints 可调用",
              callable(getattr(decision_feedback_service, "get_agent_constraints", None)))
    except Exception as exc:
        check("decision_feedback agent constraints", False, str(exc))
    swing_src = _read("backend/services/swing_agent.py")
    trend_src = _read("backend/services/trend_agent.py")
    check("SwingAgent 注入 agent_constraints", "get_agent_constraints" in swing_src)
    check("TrendAgent 注入 agent_constraints", "get_agent_constraints" in trend_src)

    # ── FactGuard paper enforce ──
    fg_src = _read("backend/services/agent_fact_guard.py")
    check("FactGuard paper→enforce", "AGENT_FACT_GUARD_PAPER_ENFORCE" in fg_src)

    # ── unified_data_pool derivatives ──
    udp_src = _read("backend/services/unified_data_pool.py")
    check("unified_data_pool 读 settings prefetch",
          "UNIFIED_DATA_POOL_KLINE_DERIVATIVES_PREFETCH" in udp_src)

    # ── runtime_tuning by_nature ──
    try:
        rt_path = os.path.join(ROOT, "data", "runtime_tuning.json")
        with open(rt_path, encoding="utf-8") as f:
            rt = json.load(f)
        check("runtime_tuning.json 含 by_nature", "by_nature" in rt)
    except Exception as exc:
        check("runtime_tuning by_nature", False, str(exc))

    # ── 证据监控落日志 ──
    check("MidLongEvidence 健康检查日志", "[MidLongEvidence]" in fa_src)

    # ── P0/P1 统一退出执行器 ──
    try:
        from backend.config import settings as s2
        check("UNIFIED_EXIT_EXECUTOR_ENABLED 默认 true", s2.UNIFIED_EXIT_EXECUTOR_ENABLED is True)
        check("min_hold_emergency_loss_pct short=8",
              s2.TIER_PROTECTION_PARAMS["short"].get("min_hold_emergency_loss_pct") == 8.0)
    except Exception as exc:
        check("exit executor settings", False, str(exc))

    try:
        from backend.services.unified_exit_executor import unified_exit_executor
        from backend.services.master_close_guard import route_exit_tier
        check("unified_exit_executor 可导入", unified_exit_executor is not None)
        check("route_exit_tier trend=1", route_exit_tier("trend_review_close") == 1)
        check("route_exit_tier master=2", route_exit_tier("master_running") == 2)
    except Exception as exc:
        check("unified_exit_executor import", False, str(exc))

    check("_run_trend_review 使用 UnifiedExitExecutor",
          "unified_exit_executor.execute(_exit_req)" in fa_src)
    check("master close 使用 UnifiedExitExecutor should_block",
          "unified_exit_executor.should_block(_exit_req)" in fa_src)
    check("hold_timeout needs_priority_ai_review",
          "needs_priority_ai_review" in _read("backend/services/hold_timeout_review_queue.py"))

    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
