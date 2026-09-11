# -*- coding: utf-8 -*-
"""短线风险回报比(RR)上调 —— P2.2（2026-09-02）。

问题：实测近14天 short tier 812 笔已平仓单，平均盈利 0.631 / 平均亏损 0.606
      → 盈亏比仅 1.042，胜率 41.4%。该胜率下需盈亏比 > 1.417 才能打平，
      即当前参数在数学上不可能盈利。

根因：开仓 TP/SL 由 market_aware_tpsl 的 regime playbook 决定，原 RR 为
      trend 1.8 / ranging 1.3 / extreme 1.2 / unknown 1.3，兜底 MIN_RR 1.3
      —— TP 与 SL 距离几乎相等；再叠加分批止盈在 TP1 就减仓 25%（赢时只
      落袋一部分、亏时全平），实现盈亏比必然趋近 1。

定标：离线回放（做多 + pwin>=0.55，2680 条，30 天，SL 固定 1.1%）
        RR1.3=+25.4bp  RR2.0=+38.4bp  RR2.3=+44.0bp  RR3.0=+52.1bp  RR4.0=+57.6bp
      取 2.0-2.5 而非更高的理由见 market_aware_tpsl 内注释（RR>=3 时止盈
      命中率<13%、持仓顶满 max_hold，收益来源是持有 beta 不是止盈兑现）。

本用例锁定：新 RR 生效、可回退、MR 不受影响、上限不再成为瓶颈。
"""
import importlib
import pytest


@pytest.fixture
def plan():
    import backend.services.exit.market_aware_tpsl as m
    importlib.reload(m)
    return m.plan_scalp_tp_sl


def _md(regime_hint=None, price=100.0, vol=0.01):
    """构造一份最小行情快照。"""
    md = {"price": price, "mark_price": price, "volatility_value": vol}
    if regime_hint:
        md.update(regime_hint)
    return md


def _rr(p):
    return (p.tp_pct / p.sl_pct) if p.sl_pct else 0.0


class TestDefaultRR:
    """默认 RR 必须达到打平线 1.417 以上。"""

    def test_min_rr_default_is_2(self, plan, monkeypatch):
        monkeypatch.delenv("SCALP_MA_MIN_RR", raising=False)
        p = plan(_md(), side="long", entry=100.0, atr_pct=0.01)
        assert _rr(p) >= 2.0 - 1e-6, (
            f"实得 RR={_rr(p):.3f}，低于打平所需的 1.417，等于回到必亏参数"
        )

    def test_rr_above_breakeven_threshold(self, plan):
        """无论哪种 regime，RR 都不得低于实测打平线 1.417。"""
        for vol in (0.006, 0.01, 0.02, 0.03):
            for side in ("long", "short"):
                p = plan(_md(vol=vol), side=side, entry=100.0, atr_pct=vol)
                assert _rr(p) >= 1.417, (
                    f"vol={vol} side={side} RR={_rr(p):.3f} < 1.417 打平线"
                )

    @pytest.mark.parametrize("env,val", [
        ("SCALP_MA_RR_TREND", 2.5),
        ("SCALP_MA_RR_RANGING", 2.0),
        ("SCALP_MA_RR_EXTREME", 2.0),
        ("SCALP_MA_RR_UNKNOWN", 2.0),
    ])
    def test_regime_rr_knobs_exist(self, env, val):
        """四个 regime 的 RR 必须可单独配置，且默认值符合定标。"""
        import inspect
        import backend.services.exit.market_aware_tpsl as m
        src = inspect.getsource(m.plan_scalp_tp_sl)
        assert env in src, f"{env} 未接出为可配置项，无法按 regime 微调或回退"
        assert f'"{env}", {val}' in src, f"{env} 默认值不是定标值 {val}"


class TestRRActuallyApplied:
    def test_tp_scales_with_sl(self, plan):
        """TP 必须随 SL 等比放大，否则 RR 只是个摆设。"""
        p1 = plan(_md(vol=0.008), side="long", entry=100.0, atr_pct=0.008)
        p2 = plan(_md(vol=0.020), side="long", entry=100.0, atr_pct=0.020)
        assert p2.sl_pct > p1.sl_pct, "更大波动应给更宽止损"
        assert p2.tp_pct > p1.tp_pct, "止损放宽时止盈须同步放远"

    def test_tp_cap_not_bottleneck(self, plan, monkeypatch):
        """上限 5.5%：SL 2.2% × RR2.5 = 5.5% 仍不该被夹。"""
        monkeypatch.setenv("SCALP_MA_RR_TREND", "2.5")
        monkeypatch.setenv("SCALP_MA_RR_UNKNOWN", "2.5")
        monkeypatch.setenv("SCALP_MA_RR_RANGING", "2.5")
        monkeypatch.setenv("SCALP_MA_RR_EXTREME", "2.5")
        p = plan(_md(vol=0.018), side="long", entry=100.0, atr_pct=0.018)
        if p.sl_pct <= 0.022:
            assert _rr(p) >= 2.0, (
                f"SL={p.sl_pct:.4f} TP={p.tp_pct:.4f} RR={_rr(p):.2f}，"
                f"上限把 RR 夹回去了"
            )

    def test_prices_consistent_with_pct(self, plan):
        """价格换算必须与百分比一致，方向不能搞反。"""
        pl = plan(_md(), side="long", entry=100.0, atr_pct=0.01)
        assert pl.tp_price > 100.0 > pl.sl_price
        assert pl.tp_price == pytest.approx(100.0 * (1 + pl.tp_pct), rel=1e-6)
        ps = plan(_md(), side="short", entry=100.0, atr_pct=0.01)
        assert ps.tp_price < 100.0 < ps.sl_price
        assert ps.tp_price == pytest.approx(100.0 * (1 - ps.tp_pct), rel=1e-6)


class TestRollback:
    def test_can_restore_old_rr(self, plan, monkeypatch):
        """一键回退到旧参数，用于出问题时快速止血。"""
        for k, v in (("SCALP_MA_RR_TREND", "1.8"),
                     ("SCALP_MA_RR_RANGING", "1.3"),
                     ("SCALP_MA_RR_EXTREME", "1.2"),
                     ("SCALP_MA_RR_UNKNOWN", "1.3"),
                     ("SCALP_MA_MIN_RR", "1.3")):
            monkeypatch.setenv(k, v)
        p = plan(_md(), side="long", entry=100.0, atr_pct=0.01)
        assert _rr(p) < 2.0, "回退开关失效，说明 RR 被硬编码而非读环境变量"

    def test_sl_bounds_unchanged(self, plan):
        """本次只动 RR 与 TP 上限，SL 夹幅必须原样保留。"""
        for vol in (0.006, 0.03):
            p = plan(_md(vol=vol), side="long", entry=100.0, atr_pct=vol)
            assert 0.005 - 1e-9 <= p.sl_pct <= 0.030 + 1e-9, (
                f"SL={p.sl_pct} 越出 [0.5%, 3.0%]"
            )


class TestGateConsistency:
    """Gate 的兜底 RR 必须与算价层同口径，否则宽 SL 单会被悄悄拉回低 RR。"""

    def test_gate_paper_min_rr_is_2(self):
        import inspect
        from backend.services.scalp.scalp_execution_gate import ScalpExecutionGate
        src = inspect.getsource(ScalpExecutionGate._ensure_min_rr)
        assert '"V5_SCALP_MIN_RR_PAPER", 2.0' in src, "纸盘兜底 RR 未同步到 2.0"
        assert '"V5_SCALP_MIN_RR", 2.0' in src, "实盘兜底 RR 未同步到 2.0"

    def test_gate_mr_stays_low(self):
        """MR 贴区间边缘做小幅回归，靠胜率不靠赔率，不能套用 2.0。"""
        import inspect
        from backend.services.scalp.scalp_execution_gate import ScalpExecutionGate
        src = inspect.getsource(ScalpExecutionGate._ensure_min_rr)
        assert '"SCALP_MR_MIN_RR", 1.0' in src, "MR 的 RR 被误改，会把 TP 推出区间"

    def test_gate_cap_aligned(self):
        """Gate 的 TP 上限须与 SCALP_MA_TP_MAX_PCT 对齐，否则它成新瓶颈。"""
        import inspect
        from backend.services.scalp.scalp_execution_gate import ScalpExecutionGate
        src = inspect.getsource(ScalpExecutionGate._ensure_min_rr)
        assert "0.055" in src, "Gate 仍用 5% 上限，与算价层 5.5% 不一致"


class TestEnvRegistered:
    """.env 与配置事实表必须同步，否则重启后行为回退且无人知晓。"""

    def _env_text(self):
        import os
        root = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", ".."))
        with open(os.path.join(root, ".env"), encoding="utf-8",
                  errors="replace") as f:
            return f.read()

    def test_env_has_new_rr(self):
        txt = self._env_text()
        assert "V5_SCALP_MIN_RR=2.0" in txt
        assert "V5_SCALP_MIN_RR_PAPER=2.0" in txt

    def test_env_max_hold_shortened(self):
        """max_hold_sec 与环境事实表同步。

        [2026-09-09 同步] 旧断言 5400（90min）已过期：现权威值为
        settings.py 默认 43200（12h，日内波段上限），.env 同值。
        """
        assert "TIER_SHORT_MAX_HOLD_SEC=43200" in self._env_text()
