"""阶段4: SwingAgent 独立路径废弃 — 集成测试

验证 mid-into-long 合并后：
  1. swing_agent 模块仍可 import（弃用警告而非错误）
  2. mlto_cycle 不再定义/调用 _swing_one
  3. master_execution 中线 SwingAgent 分支已删除（长线分支仍在）
  4. midlong_loop 仅处理 long（_run_mid 恒 False，无 SwingAgent 调度）
  5. MIDLONG_MID_VIA_MLTO 强制 False
  6. SwingAgent.analyze 不再被主路径调用（grep 静态断言）

回归保护：与 test_midview_thesis / test_decision_hub_weights / test_invalidation_close
配套运行，确认中长线整体链路无破坏。
"""
from __future__ import annotations

import os
import sys
import warnings

import pytest

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════
# 1. swing_agent 模块仍可 import（弃用警告，非错误）
# ═══════════════════════════════════════════════════════════════════
def test_swing_agent_import_emits_deprecation_warning():
    """模块导入必须发出 DeprecationWarning，但不能抛异常。"""
    # 清除已加载的模块缓存，强制重新导入以触发模块级 warning。
    for _mod in list(sys.modules):
        if _mod == "backend.services.swing_agent" or _mod.endswith(".swing_agent"):
            del sys.modules[_mod]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import importlib

        mod = importlib.import_module("backend.services.swing_agent")
        # 模块对象可用
        assert mod is not None
        # 路由辅助函数仍存在（被 master_execution / orchestrator 复用）
        assert hasattr(mod, "swing_agent")
        assert hasattr(mod.swing_agent, "is_swing_nature")
        assert hasattr(mod, "derive_swing_side")
        assert hasattr(mod, "_archive_prompt")

    # 至少有一条 DeprecationWarning 来自 swing_agent
    dep_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "swing_agent" in str(w.message)
    ]
    assert dep_warnings, (
        f"expected DeprecationWarning mentioning swing_agent, got: "
        f"{[ (w.category.__name__, str(w.message)[:80]) for w in caught ]}"
    )


# ═══════════════════════════════════════════════════════════════════
# 2. mlto_cycle 不再定义 _swing_one
# ═══════════════════════════════════════════════════════════════════
def test_mlto_cycle_no_swing_one_function():
    """_swing_one（中线独立 SwingAgent 并行执行）应已从 mlto_cycle 删除。"""
    from backend.services.full_auto import mlto_cycle

    # 函数级定义已删除
    assert not hasattr(mlto_cycle, "_swing_one"), (
        "mlto_cycle._swing_one 应已删除（阶段4：中线由 mid_view 接管）"
    )

    # 源码里不应再有 _swing_one 的 *定义或调用*（docstring/历史注释里的提及允许）
    src_path = os.path.join(
        os.path.dirname(mlto_cycle.__file__), "mlto_cycle.py"
    )
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()

    # 不应出现 "def _swing_one" 或 "pool.submit(_swing_one" 这类实际代码引用
    assert "def _swing_one" not in src, "mlto_cycle.py 不应再定义 _swing_one"
    assert "_swing_one(" not in src, "mlto_cycle.py 不应再调用 _swing_one"
    # 仍允许 import swing_agent 用于路由检测，但不应再调 swing_agent.analyze(
    assert "swing_agent.analyze(" not in src, (
        "mlto_cycle.py 不应再直接调用 swing_agent.analyze（中线 LLM 已由 mid_view 接管）"
    )


# ═══════════════════════════════════════════════════════════════════
# 3. master_execution 中线 SwingAgent 分支已删除（长线分支仍在）
# ═══════════════════════════════════════════════════════════════════
def test_master_execution_mid_swing_branch_removed_long_kept():
    """master_execution 删除中线 SwingAgent.analyze 调用，但保留长线 TrendAgent 分支。"""
    from backend.services.full_auto import master_execution

    src_path = os.path.join(
        os.path.dirname(master_execution.__file__), "master_execution.py"
    )
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()

    # 中线独立分析分支的标志性调用应已删除
    assert "swing_agent.analyze(" not in src, (
        "master_execution.py 不应再调 swing_agent.analyze（中线分支已删除）"
    )
    # 中线分支特有的 MIDLONG_MID_VIA_MLTO 引用应已删除
    assert "MIDLONG_MID_VIA_MLTO" not in src, (
        "master_execution.py 不应再引用 MIDLONG_MID_VIA_MLTO（中线分支已删除）"
    )

    # 长线 TrendAgent 分支必须保留
    assert "trend_agent.analyze_direction(" in src, (
        "master_execution.py 必须保留 trend_agent.analyze_direction（长线分支）"
    )
    assert "is_trend_nature" in src, "长线 tier 检测必须保留"

    # MidLongExecutionLane delegate 路由检测仍依赖 is_swing_nature（mid 仍要被识别为
    # midlong 委托对象，只是不再调 analyze）。
    assert "is_swing_nature" in src, (
        "is_swing_nature 路由检测应保留（MidLongExecutionLane delegate 仍需识别 mid）"
    )


# ═══════════════════════════════════════════════════════════════════
# 4. midlong_loop 仅处理 long（_run_mid 恒 False，无 SwingAgent 调度）
# ═══════════════════════════════════════════════════════════════════
def test_midlong_loop_long_only_no_mid_dispatch():
    """midlong_loop 不再调度 SwingAgent；_run_mid 强制 False。"""
    from backend.services.full_auto.loops import midlong_loop

    src_path = os.path.join(
        os.path.dirname(midlong_loop.__file__), "midlong_loop.py"
    )
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()

    # 不应再读取 due 里的 mid 来决定是否跑 LLM
    assert '_run_mid = "mid" in due' not in src, (
        "midlong_loop 不应再用 'mid' in due 决定是否跑中线 LLM"
    )
    # _run_mid 强制 False
    assert "_run_mid = False" in src, (
        "midlong_loop 应将 _run_mid 强制为 False（中线并入 long）"
    )
    # 不应再调 swing_agent.analyze
    assert "swing_agent.analyze(" not in src, (
        "midlong_loop 不应再调用 swing_agent.analyze"
    )
    # 长线（TrendAgent / MLTO thesis）路径必须保留
    assert "_maintain_mlto_theses_for_session" in src, (
        "midlong_loop 必须保留长线 thesis 维护调用"
    )


# ═══════════════════════════════════════════════════════════════════
# 5. MIDLONG_MID_VIA_MLTO 强制 False
# ═══════════════════════════════════════════════════════════════════
def test_midlong_mid_via_mlto_forced_false():
    """MIDLONG_MID_VIA_MLTO 必须恒为 False（即便环境变量设 true 也无效）。"""
    from backend.config import settings

    assert settings.MIDLONG_MID_VIA_MLTO is False, (
        f"MIDLONG_MID_VIA_MLTO 应被强制为 False，实际={settings.MIDLONG_MID_VIA_MLTO}"
    )


# ═══════════════════════════════════════════════════════════════════
# 6. maintain_mlto_theses_for_session 接受 run_mid 但不再触发 LLM
#    （smoke: 调用应不抛异常，mid key 仅占位）
# ═══════════════════════════════════════════════════════════════════
def test_maintain_mlto_theses_run_mid_is_noop_for_llm(monkeypatch):
    """run_mid=True 时不再触发 SwingAgent.analyze；mid key 仅 reserve 占位。"""
    from backend.services.full_auto import mlto_cycle
    from backend.services.full_auto.mlto_cycle import MltoCycleHost

    # 构造一个最小 host，所有 callable 都打桩
    swarm_calls = {"analyze": 0, "open": 0}

    class _SwingStub:
        def analyze(self, **kwargs):
            swarm_calls["analyze"] += 1
            raise AssertionError("swing_agent.analyze 不应被 maintain_mlto 调用")

    # 用一个简单的 namespace 模拟 session
    class _Sess:
        session_id = "test_swing_dep"
        symbols = ["BTC"]
        paper_account_id = 1
        status = "running"
        trading_mode = "paper"

    host = MltoCycleHost()
    host.mlto_handled_keys = set()
    # 关闭 thesis ledger 路径，只测 run_mid 段
    monkeypatch.setattr(
        "backend.config.settings.MIDLONG_THESIS_LEDGER_ENABLED", False
    )

    # 即便 run_mid=True，也不应触发任何 swing LLM
    mlto_cycle.maintain_mlto_theses_for_session(
        session=_Sess(),
        market_summary={"BTC": {"current_price": 100.0}},
        analyst_reports={},
        mode="paper",
        portfolio={},
        host=host,
        symbols_batch=["BTC"],
        run_mid=True,
        run_long=False,
    )
    assert swarm_calls["analyze"] == 0, (
        "run_mid=True 不应再触发 swing_agent.analyze"
    )
    # mid key 应被 reserve（占位）以避免下游重复触发
    assert "BTC:mid" in host.mlto_handled_keys, (
        "mid key 应被 reserve 占位，避免 MLTO 段误判 mid 未处理"
    )
