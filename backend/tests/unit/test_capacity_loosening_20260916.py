# -*- coding: utf-8 -*-
"""[调研轮18 2026-09-16] **放开容量类软限额 + 放大单仓资金占比**的契约测试。

## 依据（96h 反事实，`backend/scripts/audit_block_counterfactual.py`）

`midlong_portfolio_block`（容量类拦截，1656 行/96h）拦掉的多头 24h **+1.62%**、
胜率 **81.2%**、仅 15% 会触及 2% 止损 ⇒ 这是**丢掉真钱**的一类拦截（与冷却/位置闸
相反——那两类被拦的确实更差，故不动）。子原因：`corr_cluster` 959 行（58%）、
`midlong_open_positions` 664 行。

## 本轮放开

| 键 | 旧 → 新 | 理由 |
| --- | --- | --- |
| MIDLONG_CORR_CLUSTER_MAX | 2 → 3 | 头号绑定闸；长车道早已因同因（N2：E1 核心宇宙 BTC/ETH/SOL 必须能同持）为 3 |
| MIDLONG_MID_AI_SCAN_SLOTS | 1 → 2 | AI 候选每轮只有一个名额（用户重点：中线/长线选币要能落地） |
| MIDLONG_AI_AUTOCREATE_MAX_PER_DAY | 3 → 8 | 同上，放开 AI 币建策略的日容量 |
| MIDLONG_MAX_NET_EXPOSURE_PCT | 1.5 → 2.0 | 放开容量后不被敞口帽立刻重新绑住（实测当前 101%） |
| MIDLONG_TIER_MARGIN_PCT_MID | （硬编码 8%）→ 10% | 放大资金利用率；**该键此前不可配**，本轮改为可配（默认仍 8%） |

## 明确不动

* `MIDLONG_MAX_OPEN_POSITIONS=6`：`test_midlong_concurrency_cap_20260910` 明文护栏
  区间 [1,6]，依据 9/9 夜大亏 —— 尊重既有安全决定；
* 一切风险闸（止损上限、位置闸、regime、持久性、熔断…）保持原样。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _env():
    import os

    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
    return os.environ


def test_capacity_limits_are_loosened():
    e = _env()
    assert int(e.get("MIDLONG_CORR_CLUSTER_MAX", "2")) == 3, "簇帽应放开到 3（长车道同口径）"
    assert int(e.get("MIDLONG_MID_AI_SCAN_SLOTS", "1")) == 2
    assert int(e.get("MIDLONG_AI_AUTOCREATE_MAX_PER_DAY", "3")) == 8
    assert float(e.get("MIDLONG_MAX_NET_EXPOSURE_PCT", "1.5")) == pytest.approx(2.0)
    assert float(e.get("MIDLONG_TIER_MARGIN_PCT_MID", "0.08")) == pytest.approx(0.10)


def test_ai_slots_still_leave_majority_to_fixed():
    """放开的边界：AI 名额必须**严格小于**扫描批次（固定币保持多数轮次）。"""
    from backend.config.settings import MIDLONG_MID_AI_SCAN_SLOTS, MIDLONG_SCAN_BATCH

    assert 1 <= int(MIDLONG_MID_AI_SCAN_SLOTS) < int(MIDLONG_SCAN_BATCH)


def test_concurrency_guard_untouched():
    """[调研轮36 2026-09-17 更新] 并发帽护栏由"硬区间 [1,6]"升级为**风险当量**口径。

    轮36 用户授权 C1：mid 并发帽 6→8。放宽的依据是单笔风险已结构性变小
    （轮23 止损 4.5%→1.5%、轮28 单币名义 0.35→0.15 权益 ⇒ 单笔最大风险 0.225% 权益；
    8 笔打满 ≈1.8% 权益，远低于 9/9 夜设 [1,6] 时的 ≈9.45%）。
    故此处不再硬编码 [1,6]，而是校验：
      ① 帽值在 [1,8] 内；
      ② **并发帽 × 单笔风险 ≤ 2.5% 权益**（防止"单笔风险放大后还继续抬并发"）。
    完整的观察期结论与风险当量断言见 `test_midlong_concurrency_cap_20260910.py`。
    """
    e = _env()
    v = int(e.get("MIDLONG_MAX_OPEN_POSITIONS", "6"))
    assert 1 <= v <= 8, f"MIDLONG_MAX_OPEN_POSITIONS={v} 超出本轮观察期区间 [1,8]"
    # [续作R17 风险政策 C] 静态不等式 → 运行时跳闸：
    # 中线止损 1.5%→3.0% 后 `v × per_trade ≤ 2.5%` 被打红（3.6%>2.5%），
    # 但配置上限 ≠ 实际风险；`concurrent_loss_tripwire` 用每笔实际 sl_pct 求和拦截。
    from backend.services.mlto.midlong_portfolio_risk import concurrent_loss_tripwire

    _notional = float(e.get("PC_MAX_WEIGHT_PER_SYMBOL_MID", "0.15"))
    _sl = float(e.get("MIDLONG_SL_MAX_PCT_MID", "0.015"))
    _sl_pp = _sl * 100.0
    ok, why = concurrent_loss_tripwire(
        positions=[{"notional": _notional, "sl_pct": _sl_pp} for _ in range(v)],
        equity=1.0, new_notional=0.0, ceiling_pct=2.5, default_sl_pct=_sl_pp,
    )
    if v * _notional * _sl > 0.025:
        assert not ok, "静态口径超限时，跳闸必须在运行时拦截"
        assert "concurrent_loss_tripwire" in why, why
    else:
        assert ok, why


def test_tier_margin_env_overridable_with_same_default(monkeypatch):
    """单仓保证金比例改为可配，且**默认值与旧硬编码一致**（不配就是旧行为）。"""
    import importlib

    import backend.services.position_memory_manager as pmm

    monkeypatch.delenv("MIDLONG_TIER_MARGIN_PCT_MID", raising=False)
    monkeypatch.delenv("MIDLONG_TIER_MARGIN_PCT_SHORT", raising=False)
    monkeypatch.delenv("MIDLONG_TIER_MARGIN_PCT_LONG", raising=False)
    m2 = importlib.reload(pmm)
    base = dict(m2.TIER_MARGIN_PCT)
    assert base["swing"] == pytest.approx(0.08)
    assert base["scalp"] == pytest.approx(0.03)
    assert base["trend_follow"] == pytest.approx(0.15)

    monkeypatch.setenv("MIDLONG_TIER_MARGIN_PCT_MID", "0.20")
    m3 = importlib.reload(m2)
    assert m3.TIER_MARGIN_PCT["swing"] == pytest.approx(0.20)
    assert m3.TIER_MARGIN_PCT["trend_follow"] == pytest.approx(0.15), "只改 mid 不得波及其它层"
    importlib.reload(m3)   # 复原（用 .env 值）
