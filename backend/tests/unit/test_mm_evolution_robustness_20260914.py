# -*- coding: utf-8 -*-
"""[2026-09-14 F116] 自进化上线决策必须对"成交桶归属口径"稳健。

背景（本轮量化）：
  - 成交桶 `market_trades_aggregated` 按**落库时刻**分桶（滞后 1~13s）、15s 网格只有
    47.5% 被填充 ⇒ "哪笔成交打到哪张单"存在 ±15~30s 不确定性；
  - 实测把回放的可见性滞后从 8.8s 换成 4.4s / 17.6s，**同一配置**的全窗净额在
    0.46 ~ 2.20bp 间摆动（**4.8×**）✗✗；
  - 而 `should_deploy` 此前只看**单口径**的 net_bp/net_usd，容忍带 FULL_TOL=2%
    ⇒ 一个"改进"完全可能是口径噪声，上线决策会被噪声驱动 ✗。

同时修两处**保真度**缺陷（同一根因，F108c 已证明其影响量级）：
  1. `_evaluate` 只喂切片数据、不传 `vol_baseline` ⇒ 回放用切片窗口现算基准 ⇒ σ 被
     系统性归零（σ=0 占 63%）⇒ 挂宽偏窄、成交偏多 ⇒ 候选评分偏乐观；
  2. 未传 `enforce_lane_limits`（实盘 `.env` 已武装）与复利腿量口径。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _cand(usd=10.0, bp=1.0, fills=200):
    return {"net_usd": usd, "net_bp": bp, "fills": fills, "max_dd_pct": 1.0}


def test_robust_gate_rejects_delay_sensitive_candidate():
    """在 8.8s 口径"更好"、但在 17.6s 口径大幅回退的候选必须被拒（fail-closed）。"""
    from backend.services.market_maker.evolution import should_deploy

    robust = {"delays_ms": [4400.0, 8800.0, 17600.0],
              "cand_usd": [11.0, 12.0, 4.0],      # 17.6s 下比在位差很多
              "inc_usd": [10.0, 10.0, 10.0]}
    ok, why = should_deploy(_cand(usd=12.0, bp=1.5), _cand(usd=10.0, bp=1.0),
                            robust=robust)
    assert not ok, "回退口径存在时必须拒绝"
    assert "多口径不稳" in why, why


def test_robust_gate_accepts_consistent_candidate():
    from backend.services.market_maker.evolution import should_deploy

    robust = {"delays_ms": [4400.0, 8800.0, 17600.0],
              "cand_usd": [12.0, 12.5, 11.5],
              "inc_usd": [10.0, 10.0, 10.0]}
    ok, why = should_deploy(_cand(usd=12.5, bp=1.5), _cand(usd=10.0, bp=1.0),
                            robust=robust)
    assert ok, why
    assert "稳健" in why


def test_robust_gate_requires_majority_wins():
    """只有一个口径更优（其余持平）⇒ 胜率不足，拒绝。"""
    from backend.services.market_maker.evolution import should_deploy

    robust = {"delays_ms": [4400.0, 8800.0, 17600.0],
              "cand_usd": [9.95, 12.0, 9.95],     # 仅 8.8s 口径更优，其余略低于在位
              "inc_usd": [10.0, 10.0, 10.0]}
    ok, why = should_deploy(_cand(usd=12.0, bp=1.5), _cand(usd=10.0, bp=1.0),
                            robust=robust)
    assert not ok and "胜率" in why, why


def test_evaluate_passes_live_parity_flags():
    """源码契约：候选评估必须与实盘同源（锚定基准 / 武装闸门 / 复利 / 可见性滞后）。"""
    import inspect
    from backend.services.market_maker import evolution as ev

    src = inspect.getsource(ev.run_evolution_round)
    assert "vol_baseline=(anchored_vb or None)" in src, "必须传实盘同源的波动基准"
    assert "enforce_lane_limits=True" in src, "必须武装车道闸门（实盘已武装）"
    assert "tick_delay_ms=delay_ms" in src, "必须使用成交桶可见性滞后"
    assert "replay_baseline" in src, "基准应取自注册表 replay_baseline"
    assert '"tmk"' in src, "切片必须携带 created_at（可见性过滤依赖它）"


def test_robust_delays_span_measured_lag():
    """口径网格必须覆盖实测滞后分布（p10≈1.6s、中位≈7.6s、p90≈13.5s）。"""
    from backend.services.market_maker.evolution import ROBUST_DELAYS_MS

    assert len(ROBUST_DELAYS_MS) >= 3
    assert min(ROBUST_DELAYS_MS) <= 5000.0, "要覆盖偏早落库"
    assert max(ROBUST_DELAYS_MS) >= 15000.0, "要覆盖偏晚落库"
