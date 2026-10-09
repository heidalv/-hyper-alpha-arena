# -*- coding: utf-8 -*-
"""[F333 2026-09-18 复查回归] 架构不变量：把本次复查修掉的"静默死链"钉成测试。

背景：这几处缺陷的共同形态是**代码在、测试绿、运行时静默失效**（无日志、无告警）：
  · `prompt_context/__init__.py` 0 字节 ⇒ 备用 LLM 车道导入必失败（P0）；
  · `hybrid_scoring/evidence.py` 目录拼错 ⇒ thesis 数据恒空 ⇒ 流B 零产出；
  · `unified_strategy/weekly_loop.py` 未定义变量/幽灵 import ⇒ 周报块永久坏块、IC 恒 null；
  · `factor_decay_monitor.get_factor_weight_penalty` 缺上限 ⇒ 降权变放大；
  · `signal_feedback_tracker` 把 NULL 当 0 计入均值；
  · `master_execution` 读错键名 ⇒ factor_votes 恒空。
本文件对应报告 §6.3 的 **A6 判据（禁止静默退化，每处留一条回归）**。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


# ── 1. P0：备用 LLM 车道的 prompt 组装器必须可导入 ──────────────────────────

def test_prompt_context_exports_builder():
    from backend.services.prompt_context import BuildInput, PromptContextBuilder
    assert PromptContextBuilder is not None and BuildInput is not None


# ── 2. 流B：thesis 数据目录必须指向真实位置 ─────────────────────────────────

def test_thesis_dir_points_to_existing_data_dir():
    from backend.services.hybrid_scoring import evidence
    assert evidence._UNIFIED_DIR.exists(), f"thesis 目录不存在: {evidence._UNIFIED_DIR}"
    assert evidence._UNIFIED_DIR.name == "ai_coin_unified"
    # 必须是 services/data/ai_coin_unified（而不是 hybrid_scoring/data/...）
    assert evidence._UNIFIED_DIR.parent.name == "data"
    assert "hybrid_scoring" not in str(evidence._UNIFIED_DIR).replace("hybrid_scoring\\evidence", "")


# ── 3. 周报：thesis 通道块不得再返回坏块 ────────────────────────────────────

def test_weekly_thesis_block_is_available():
    from backend.services.unified_strategy.weekly_loop import _thesis_channel_block
    r = _thesis_channel_block(None)
    assert r.get("available") is True, r
    assert "agent_scores" in r, "修复后应显式返回 agent_scores 字段"


# ── 4. 衰减惩罚：只降不升（不得 >1.0） ──────────────────────────────────────

def test_decay_penalty_never_amplifies():
    from backend.services.factor_engine.factor_decay_monitor import FactorDecayMonitor
    m = object.__new__(FactorDecayMonitor)
    for cur, hist in ((0.0124, 0.001), (0.0136, 0.010), (0.5, 0.001)):
        m._decay_status = {"f": SimpleNamespace(recommendation="reduce",
                                               current_ic=cur, historical_ic=hist)}
        p = m.get_factor_weight_penalty("f")
        assert 0.3 <= p <= 1.0, f"reduce 分支必须落在 [0.3,1.0]，实测 {p}（cur={cur} hist={hist}）"


# ── 5. 归因口径：不得再把 NULL 当 PnL=0 计入均值 ────────────────────────────

def test_no_null_as_zero_in_feedback_tracker():
    src = (ROOT / "backend" / "services" / "signal_feedback_tracker.py").read_text(
        encoding="utf-8")
    code_lines = [ln for ln in src.splitlines()
                  if "trade_pnl_pct or 0" in ln and not ln.lstrip().startswith("#")]
    assert not code_lines, f"仍有把 NULL 当 0 的代码行: {code_lines}"


# ── 6. factor_votes：消费端必须认生产端的真实键名 ──────────────────────────

def test_master_execution_reads_factor_contrib_key():
    src = (ROOT / "backend" / "services" / "full_auto" / "master_execution.py").read_text(
        encoding="utf-8")
    assert 'get("factor_contrib")' in src, "消费端未接受生产端键名 factor_contrib"


# ── 7. 学习端静默失败出口必须有日志（不得只有 return False） ────────────────

def test_learning_false_branches_are_logged():
    src = (ROOT / "backend" / "services" / "strategy_learning_service.py").read_text(
        encoding="utf-8")
    assert src.count("因子权重未更新") >= 3, "三个失败出口应各有一条可区分日志"


# ── 8. 衰减断路器：不得批量归零；新建档不得播种 0 IC（F336） ─────────────────

def _monitor(n_zeroable: int = 3, n_keep: int = 5):
    """构造一个池：n_zeroable 个 historical_ic=0 的因子（retire 时会归零）+ n_keep 个正常因子。

    注意：池内**必须真的存在可归零因子**，否则断路器分支根本不会被触发 ⇒
    会得出"断路器没生效"或"断路器已生效"的假结论（本会话踩过一次）。
    """
    import os
    from types import SimpleNamespace
    from backend.services.factor_engine.factor_decay_monitor import DecayStatus, FactorDecayMonitor

    m = object.__new__(FactorDecayMonitor)
    st = {}
    for i in range(n_zeroable):
        st[f"z{i}"] = DecayStatus(factor_id=f"z{i}", current_ic=0.05, historical_ic=0.0,
                                  decay_rate=0.0, half_life_days=0.0, trend="declining",
                                  recommendation="reduce")
    for i in range(n_keep):
        st[f"k{i}"] = DecayStatus(factor_id=f"k{i}", current_ic=0.05, historical_ic=0.05,
                                  decay_rate=0.0, half_life_days=1.0, trend="stable",
                                  recommendation="keep")
    m._decay_status = st
    m._save_status = lambda: None
    os.environ.pop("FACTOR_DECAY_ALLOW_MASS_RETIRE", None)
    return m


def test_decay_breaker_blocks_mass_retire():
    m = _monitor(n_zeroable=3, n_keep=5)          # 3/8 = 37.5% > 30% ⇒ 必须被拦
    m.apply_incremental_pnl({"z0": -0.01, "z1": -0.01, "z2": -0.01})
    pens = [m.get_factor_weight_penalty(k) for k in ("z0", "z1", "z2")]
    assert all(p == 0.3 for p in pens), f"批量归零未被拦下或降级未生效: {pens}"


def test_decay_breaker_allows_small_retire_and_override():
    import os
    m = _monitor(n_zeroable=3, n_keep=9)          # 3/12 = 25% ≤ 30% ⇒ 放行归零
    m.apply_incremental_pnl({"z0": -0.01, "z1": -0.01, "z2": -0.01})
    assert m.get_factor_weight_penalty("z0") == 0.0, "未超限时应允许归零"

    m2 = _monitor(n_zeroable=3, n_keep=5)
    os.environ["FACTOR_DECAY_ALLOW_MASS_RETIRE"] = "1"
    try:
        m2.apply_incremental_pnl({"z0": -0.01, "z1": -0.01, "z2": -0.01})
        assert m2.get_factor_weight_penalty("z0") == 0.0, "逃生阀应放行批量归零"
    finally:
        os.environ.pop("FACTOR_DECAY_ALLOW_MASS_RETIRE", None)


def test_new_decay_entry_does_not_seed_zero_ic():
    m = _monitor()
    m.apply_incremental_pnl({"brand_new": -0.01})
    st = m._decay_status["brand_new"]
    assert st.historical_ic > 0.01, f"新建档播种 historical_ic={st.historical_ic} ⇒ 双确认自动成立"
    assert m.get_factor_weight_penalty("brand_new") > 0.0, "首触不得直接归零"

