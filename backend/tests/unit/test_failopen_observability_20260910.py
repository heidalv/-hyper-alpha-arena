# -*- coding: utf-8 -*-
"""[2026-09-10 第三轮审计] 「fail-open 静默放行」可观测性契约测试。

背景：闸函数本身把 fail-open 理由写得很清楚，但**调用方**用 `logger.debug`
记录「闸失效→放行」，生产日志级别 INFO ⇒ 完全静默（实测 19 处，中长线入口 8 处）。
本测试锁住：中长线入口路径的 fail-open 必须**可见**（warning/info，不得为 debug）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# 中长线入口路径上必须可见的 fail-open 日志（文件 → 消息片段）
REQUIRED = [
    ("backend/services/full_auto/midlong_executor.py", "swing 共识闸异常(fail-open)"),
    ("backend/services/full_auto/midlong_executor.py", "熔断闸检查跳过(fail-open)"),
    ("backend/services/full_auto/midlong_executor.py", "图审闸检查跳过(fail-open)"),
    ("backend/services/full_auto/midlong_executor.py", "位置闸检查跳过(fail-open)"),
    ("backend/services/full_auto/midlong_circuit_gate.py", "learned 多头特征读取失败(fail-open)"),
    ("backend/services/full_auto/midlong_circuit_gate.py", "检查异常(fail-open)"),
    ("backend/services/factor_engine/midlong_flow_gate.py", "flow 获取失败(fail-open)"),
    ("backend/services/factor_engine/midlong_factor_route.py", "资金流门跳过(fail-open)"),
    # ── [§51.7 2026-09-10 第 14 轮] **出场侧**补漏：§41.2 只覆盖了"入口路径"4 个模块，
    # 但同一缺陷类在出场/反向路径上还有 2 处（都是 mid/long 相关、都是静默 debug）：
    ("backend/services/unified_exit_executor.py", "免疫检查异常(fail-open)"),
    ("backend/services/full_auto/paper_execution.py", "ai_reverse 免疫检查异常(fail-open)"),
    # ── [§59 2026-09-10 第 21 轮] 系统性补漏：`chart_gate` 等 guard 类 fail-open
    # 仍是 debug（§41.2 只覆盖 4 个模块、§51.7 补了出场侧 2 处）。
    ("backend/services/full_auto/midlong_chart_gate.py", "信号查询失败(fail-open"),
    ("backend/services/full_auto/midlong_chart_gate.py", "亏损平仓查询失败(fail-open"),
    ("backend/services/full_auto/midlong_position_manager.py", "review_min_hold 检查异常(fail-open"),
    ("backend/services/full_auto/midlong_position_manager.py", "论题离场检查跳过(fail-open"),
    ("backend/services/full_auto/paper_execution.py", "MidLongExposureCap] 检查异常(fail-open"),
    ("backend/services/full_auto/midlong_helpers.py", "TierCircuit] 检查跳过(fail-open"),
    ("backend/services/full_auto/midlong_helpers.py", "冷却检查跳过(fail-open"),
    ("backend/services/risk_constitution.py", "宪法检查即放行"),
    ("backend/services/decision_core/pipeline.py", "MidLongEvGate] 跳过(fail-open"),
    ("backend/services/decision_core/pipeline.py", "concurrent cap 跳过(fail-open"),
    ("backend/services/factor_engine/midlong_factor_route.py", "持仓检查跳过"),
    ("backend/services/full_auto/master_execution.py", "实盘配额检查跳过(fail-open"),
]


def test_midlong_failopen_logs_are_visible():
    bad = []
    for rel, snippet in REQUIRED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        hit = [ln for ln in text.splitlines() if snippet in ln]
        assert hit, f"{rel} 里找不到 fail-open 日志: {snippet}"
        line = hit[0]
        if "logger.debug(" in line:
            bad.append(f"{rel}: {snippet} 仍是 logger.debug（生产不可见）")
    assert not bad, "以下 fail-open 仍是静默 debug:\n" + "\n".join(bad)


def test_no_failopen_debug_left_on_entry_path():
    """中长线入口/出场模块里不应再出现 `logger.debug(... fail-open ...)`。"""
    mods = [
        "backend/services/full_auto/midlong_executor.py",
        "backend/services/full_auto/midlong_circuit_gate.py",
        "backend/services/factor_engine/midlong_flow_gate.py",
        "backend/services/factor_engine/midlong_factor_route.py",
        # [§51.7] 出场侧：软退出免疫闸（mid/long）与 long-tier 反向免疫闸
        "backend/services/unified_exit_executor.py",
        "backend/services/full_auto/paper_execution.py",
        # [§59] guard 类模块收口：图审闸/持仓管理/宪法/决策管线/主执行
        "backend/services/full_auto/midlong_chart_gate.py",
        "backend/services/full_auto/midlong_position_manager.py",
        "backend/services/risk_constitution.py",
        "backend/services/decision_core/pipeline.py",
        "backend/services/full_auto/master_execution.py",
    ]
    offenders = []
    for rel in mods:
        for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"logger\.debug\(", line) and re.search(r"fail-open|failopen", line, re.I):
                offenders.append(f"{rel}:{i}")
    assert not offenders, "仍有 debug 级 fail-open 日志: " + ", ".join(offenders)


def test_gate_reachability_tool_grades_failopen():
    """工具必须能区分「意图明确」与「裸 except 放行」——否则会像首版那样误报。"""
    import importlib
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    m = importlib.import_module("audit_gate_reachability")
    src = (ROOT / "backend" / "scripts" / "audit_gate_reachability.py").read_text(encoding="utf-8")
    assert "fail_open_documented" in src and "INTENT_RX" in src
    assert hasattr(m, "scan_module") and hasattr(m, "count_calls")
    # 别名映射必须存在（首版盲点：aliased import 被漏计）
    assert hasattr(m, "build_alias_map")
    assert m.build_alias_map("from x import gate_a as _g\n") == {"_g": "gate_a"}


def test_alias_map_parsing_multiline_import():
    import importlib
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    m = importlib.import_module("audit_gate_reachability")
    alias = m.build_alias_map(
        "        from backend.services.tier_circuit_breaker import (\n"
        "            is_tier_open_blocked as _tier_cb_blocked,\n"
        "        )\n"
    )
    assert alias.get("_tier_cb_blocked") == "is_tier_open_blocked"


def test_exit_immunity_failopen_is_logged_at_runtime(monkeypatch, caplog):
    """[§51.7] 运行时 A/B 验证出场免疫闸：

    A) 闸正常判定「该免疫」→ 返回 `midlong_soft_exit_immune`（免疫生效，拦住软退出）；
    B) 闸抛异常 → **不再返回该免疫结果**（即免疫没生效 = fail-open 放行软退出），
       但必须在 WARNING 级别留痕（修复前是 debug，生产完全静默）。

    注意口径：fail-open 的含义是「免疫这道闸没生效」，下游别的闸仍可能拦住本次退出
    （本例下游 hardfact 闸就会拦），所以断言的是 **event_type 不再是免疫结果**，
    而不是 `blocked is False`。
    """
    import logging

    from backend.services import risk_band_resolver as rbr
    from backend.services.unified_exit_executor import (
        ExitExecuteRequest, UnifiedExitExecutor,
    )

    def _req():
        return ExitExecuteRequest(
            db=None, account_id=14, symbol="ETHUSDT", action="close",
            pos={"timeframe_tier": "mid", "trade_nature": "swing", "entry_price": 100.0,
                 "size": 1.0, "side": "long"},
            exit_channel="master_running", reason="master_running_close",
        )

    ex = UnifiedExitExecutor()

    # A) 免疫闸正常工作
    monkeypatch.setattr(rbr, "is_close_reason_blocked_for_midlong",
                        lambda *a, **k: True, raising=True)
    ok = ex.should_block(_req())
    assert ok.blocked is True and ok.event_type == "midlong_soft_exit_immune", ok

    # B) 免疫闸异常 → fail-open（免疫不生效）+ WARNING 留痕
    def _boom(*a, **k):
        raise RuntimeError("settings 读取失败")

    monkeypatch.setattr(rbr, "is_close_reason_blocked_for_midlong", _boom, raising=True)
    with caplog.at_level(logging.WARNING):
        broken = ex.should_block(_req())

    assert broken.event_type != "midlong_soft_exit_immune", \
        f"闸异常时不得声称免疫生效: {broken}"
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("免疫检查异常(fail-open)" in m for m in msgs), f"未在 WARNING 留痕: {msgs}"
