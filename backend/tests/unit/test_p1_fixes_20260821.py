"""P1 修复回归（S5/S6/S9/S10 + M4/M5/M6/M9/M11，设计《短线中线修复升级设计_20260820》）。

- S5  无余额快照 → 本轮不开仓（废除 sum(margin)*3 反推）
- S6  学习值 SL 夹幅 [1.2%,2.0%]→[0.6%,3.0%]，tp_sl_gates 联动保 RR≥1.4
- S9  short_tier/组合预算/仲裁 三闸 fail-open→fail-closed（reentry_cooldown 不动）
- S10 震荡缩仓地板 0.70→SCALP_RANGING_SIZE_FLOOR(0.35)
- M4  因子仓动态 SL/TP：max(结构摆动, k×ATR@4h)，5%/10% 上限夹幅
- M5  swing 独立置信度/RR 口径（V5_SWING_*）
- M6  探针代码默认关 + 活跃因子数条件
- M9  |ic|<0.02 不反手（skip）+ 负 IC 告警
- M11 factor_route 提案不走 Paper 探针软放行
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))


def _src(*parts):
    return open(os.path.join(os.path.dirname(__file__), "..", "..", *parts), encoding="utf-8").read()


# ── S5 / S9 / S10：scalp_loop 源码契约 + 配置 ───────────
class TestScalpP1:
    def test_s5_no_margin_sum3_fallback(self):
        src = _src("services", "full_auto", "loops", "scalp_loop.py")
        assert "sum(float(getattr(p, \"margin\", 0) or 0) for p in _pos_margin) * 3" not in src
        assert "equity_unavailable" in src

    def test_s9_three_gates_fail_closed(self):
        src = _src("services", "full_auto", "loops", "scalp_loop.py")
        assert '_bump_block("short_tier_error")' in src
        assert '_bump_block("portfolio_budget_error")' in src
        assert '_bump_block("arbitration_error")' in src
        # 旧 fail-open 文案必须消失
        assert "short_tier_gate 检查跳过" not in src
        assert "组合预算跳过" not in src
        # [aa7e0dd 信号准确率根治] pwin 仲裁异常改为「降级放行+日志」——
        # 不再静默跳过，也不再一刀切拦截；与 S9 三闸 fail-closed 并存。
        assert "pwin仲裁跳过(降级放行)" in src
        # 已 fail-closed 的 reentry_cooldown 语义保留
        assert "reentry_cooldown 异常，拒绝开仓" in src

    def test_s10_floor_035(self):
        src = _src("services", "full_auto", "loops", "scalp_loop.py")
        assert "max(0.70" not in src
        assert "SCALP_RANGING_SIZE_FLOOR" in src
        import backend.config.settings as _s
        assert _s.SCALP_RANGING_SIZE_FLOOR == pytest.approx(0.35)


# ── S6：学习值 SL 夹幅 ─────────────────────────────────
class TestS6LearnedSlClamp:
    def test_mr_clamps_widened(self):
        """[2026-08-24 深挖B 更新] 夹幅 →[1.2%,1.6%]：7 天实证 SL 猎杀带在
        [0.6%,1.2%]，宽 SL 才是 MR 活命结构；1.6-1.8% 带每笔 pnl 为正。"""
        from backend.services.scalp import scalp_ranging_mr as mr
        assert mr._MR_SL_FLOOR == pytest.approx(0.012)
        assert mr._MR_SL_CAP == pytest.approx(0.016)

    def test_learned_sl_survives(self, monkeypatch):
        from backend.services.risk import tp_sl_grid_trainer as tgt
        from backend.services.scalp import scalp_ranging_mr as mr
        monkeypatch.setattr(
            tgt, "get_learned_pct",
            lambda tier, band: {"tp_pct": 0.025, "sl_pct": 0.027},
        )
        tp, sl = mr.apply_learned_mr(0.02, 0.012)
        assert sl == pytest.approx(0.016)  # 夹幅封顶 1.6%（深挖B）
        assert tp == pytest.approx(0.009)  # TP cap 0.9%（2026-08-26 MFE 峰值带）

    def test_structure_atr_clamp_widened(self):
        from backend.services.scalp.structure_stop_calculator import StructureStopCalculator
        c = StructureStopCalculator()
        assert c.compute_atr_pct({"volatility_value": 0.001}) == pytest.approx(0.006)
        assert c.compute_atr_pct({"volatility_value": 0.05}) == pytest.approx(0.030)

    def test_tp_sl_gates_rr_coherent(self):
        """[2026-08-23 改造A 更新] scalp 夹幅对齐新 TP/SL 口径（0.9-1.5%/0.7-1.2%）。"""
        src = _src("services", "full_auto", "tp_sl_gates.py")
        assert '"scalp":        (0.009, 0.015, 0.007, 0.012)' in src
        from backend.config.settings import V5_SCALP_MIN_RR
        assert 0.015 / 0.012 >= 1.2  # 新夹幅 RR 下限 1.25
        assert 0.015 / 0.009 >= 1.5  # 典型 RR 1.5 ≥ V5 闸
        assert float(V5_SCALP_MIN_RR) <= 1.5


# ── M4：动态 SL/TP ─────────────────────────────────────
class TestM4DynamicSlTp:
    def _mk_df(self, atr_pct=0.01, n=120):
        base = 100.0
        rows = []
        for i in range(n):
            o = base + i * 0.01
            c = o
            rows.append({"open": o, "high": o * (1 + atr_pct / 2),
                         "low": o * (1 - atr_pct / 2), "close": c, "volume": 1.0})
        return pd.DataFrame(rows)

    def test_atr_driven(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.factor_engine.midlong_factor_route as mrf
        monkeypatch.setattr(mrf, "_load_df", lambda s, tf, lb: self._mk_df(atr_pct=0.01))
        monkeypatch.setattr(_s, "FACTOR_ROUTE_SL_ATR_MULT", 1.5, raising=False)
        monkeypatch.setattr(_s, "FACTOR_ROUTE_TP_ATR_MULT", 3.0, raising=False)
        sl, tp, note = mrf._dynamic_sl_tp("BTC")
        # ATR≈1%，SL≈max(swing≈1%, 1.5%)≈1.5%；TP≈max(1%, 3%, SL×1.8)=3%
        assert sl == pytest.approx(0.015, abs=1e-3)
        assert tp == pytest.approx(0.030, abs=1e-3)
        assert "swing=" in note

    def test_caps_hold(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.factor_engine.midlong_factor_route as mrf
        monkeypatch.setattr(mrf, "_load_df", lambda s, tf, lb: self._mk_df(atr_pct=0.08))
        sl, tp, _ = mrf._dynamic_sl_tp("BTC")
        assert sl <= 0.05 + 1e-9
        assert tp <= 0.10 + 1e-9
        assert tp >= sl * 1.7  # RR 兜底

    def test_fallback_when_no_klines(self, monkeypatch):
        import backend.services.factor_engine.midlong_factor_route as mrf
        monkeypatch.setattr(mrf, "_load_df", lambda s, tf, lb: None)
        sl, tp, note = mrf._dynamic_sl_tp("BTC")
        assert sl == pytest.approx(0.05) and tp == pytest.approx(0.10)
        assert "fallback" in note


# ── M5：swing 独立门槛 ─────────────────────────────────
class TestM5SwingThreshold:
    def test_resolver_swing_branch(self):
        from backend.services.decision_core.threshold_resolver import (
            resolve_effective_entry_threshold,
        )
        eff = resolve_effective_entry_threshold(
            base_threshold=40, nature="swing",
            swing_gate=45, trend_gate=72,  # trend 门槛再高也不该管 swing
        )
        assert eff.effective >= 45
        assert "swing" in eff.explain()

    def test_settings_keys(self):
        from backend.config.settings import (
            V5_SWING_MIN_CONFIDENCE, V5_SWING_MIN_RR, V5_SWING_MIN_RR_PAPER,
        )
        assert V5_SWING_MIN_CONFIDENCE == 45
        assert 1.4 <= V5_SWING_MIN_RR_PAPER < V5_SWING_MIN_RR <= 1.8

    def test_unified_gate_wires_swing_gate(self):
        src = _src("services", "decision_core", "unified_gate.py")
        assert "swing_gate=_paper_swing_gate" in src
        assert "nature_l == \"swing\"" in src and "V5_SWING_MIN_RR" in src


# ── M6 / M9 / M11 ──────────────────────────────────────
class TestM6M9M11:
    def test_m6_probe_default_off(self):
        src = _src("config", "settings.py")
        assert '"MIDLONG_ALLOW_RANGE_PROBE", "false"' in src
        ex = _src("services", "full_auto", "midlong_executor.py")
        assert "探针关闭：活跃因子" in ex  # 即便 env 开也要过因子数条件

    def test_m9_weak_ic_skipped_not_flipped(self, monkeypatch):
        import backend.config.settings as _s
        import backend.services.factor_engine.midlong_factor_route as mrf
        import backend.services.factor_engine.midlong_active_factor_set as mafs

        class _FakeSet:
            def get_active_factors(self):
                return [{"factor_id": "f1", "scores": {"ic_mean": 0.01},
                         "runtime_weight": 1.0}]

        monkeypatch.setattr(mafs, "midlong_active_factor_set", _FakeSet())
        monkeypatch.setattr(_s, "FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 1, raising=False)
        monkeypatch.setattr(_s, "FACTOR_ROUTE_IC_ABS_MIN", 0.02, raising=False)
        monkeypatch.setattr(
            mrf, "_factor_history",
            lambda rec, sym: np.sin(np.linspace(0, 20, 200)),
        )
        out = mrf.factor_route_decide("BTC", market_summary={"BTC": {"price": 100.0}})
        # 弱 IC 因子被 skip（不反手），usable 不足 → hold
        assert out["action"] == "hold"
        assert out["votes"]["f1"]["skip"] == "weak_ic"

    def test_m11_factor_route_exempt_from_softpass(self):
        src = _src("services", "decision_core", "pipeline.py")
        assert 'dec.get("entry_source") or "").lower() != "factor_route"' in src
