# -*- coding: utf-8 -*-
"""fast_cut 只砍确实走坏的仓位（2026-09-02 P2.1）。

问题：原条件仅要求「持仓超时窗 且 peak<0.3% 且 unrealized<0.3%」，把两类完全
不同的仓位一并平掉 ——
  A. 微盈微亏来回震荡、还没发育的（本可继续持有）；
  B. 方向确实错了的（该认错）。

证据：
- 近 30 天 801 笔时间类出场中，453 笔（57%）曾浮盈 >0.2%、272 笔（34%）曾 >0.5%
  —— 它们本来赚钱，被时间赶出场时已回吐（MFE 遥测，覆盖率 73.4%）。
- 离线回放（做多+pwin>=0.55，2542 条，分段稳健性 robust）：有效持仓 1800s→3600s
  净收益 +15.14bp → +33.21bp；同批 timeout 出场自身净 +20.87bp 为正，说明"到时
  平仓"是在锁利，问题在**砍得太早太宽**，而非时间出场本身。

故新增"确实走坏"条件。本用例锁定该语义，并保证可一键回退。
"""
import pytest


def _ctx(**kwargs):
    from backend.services.exit.exit_types import PositionContext
    defaults = dict(
        position_id=11, symbol="ETH", tier="short", side="long",
        entry_price=100.0, current_price=100.5, quantity=1.0,
        unrealized_pnl_pct=0.5, peak_pnl_pct=0.5, hold_seconds=120,
        atr_pct=2.0, sl_price=98.8, tp_price=102.0,
    )
    defaults.update(kwargs)
    return PositionContext(**defaults)


def _eval(**kw):
    from backend.services.exit.tier_exit_strategies import ShortTierExit
    return ShortTierExit().evaluate(_ctx(**kw))


@pytest.fixture(autouse=True)
def _fixed_window(monkeypatch):
    """钉住窗口与门槛，避免用例随 .env 漂移。"""
    monkeypatch.setenv("SCALP_EXIT_FAST_CUT_ENABLED", "true")
    monkeypatch.setenv("SCALP_EXIT_FAST_CUT_MIN", "30")
    monkeypatch.setenv("SCALP_EXIT_FAST_CUT_PEAK_PCT", "0.3")
    monkeypatch.setenv("SCALP_EXIT_FAST_CUT_LOSS_PCT", "0.2")


def _is_fast_cut(d) -> bool:
    return bool(d) and d.action == "close" and "fast_cut" in (d.reason or "")


class TestGoneBadRequired:
    """核心变更：无浮盈还不够，必须确实在亏。"""

    def test_gone_bad_is_cut(self):
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.3, peak_pnl_pct=0.1)
        assert _is_fast_cut(d), "明确走坏的仓位仍须认错出清"

    def test_flat_is_spared(self):
        """浮盈 0 附近震荡 —— 这正是 57% 被误砍的那批。"""
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=0.0, peak_pnl_pct=0.15)
        assert not _is_fast_cut(d), "微盈微亏震荡的仓位不应被 fast_cut"

    def test_slight_loss_above_threshold_spared(self):
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.1, peak_pnl_pct=0.1)
        assert not _is_fast_cut(d), "浮亏未达 -0.2% 不应认错"

    def test_slight_profit_spared(self):
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=0.2, peak_pnl_pct=0.25)
        assert not _is_fast_cut(d)

    def test_boundary_is_inclusive(self):
        """恰好 -0.2% 应触发（<= 而非 <），避免边界处行为含糊。"""
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.2, peak_pnl_pct=0.1)
        assert _is_fast_cut(d)


class TestOriginalGuardsIntact:
    """原有三个条件不得失效，否则会砍掉正在赚钱的仓位。"""

    def test_before_window_not_cut(self):
        d = _eval(hold_seconds=10 * 60, unrealized_pnl_pct=-0.5, peak_pnl_pct=0.1)
        assert not _is_fast_cut(d), "未到时间窗不得 fast_cut"

    def test_high_peak_not_cut(self):
        """曾冲上 0.5% 浮盈 → 有发育证据，交给 trailing/breakeven 处理。"""
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.5, peak_pnl_pct=0.5)
        assert not _is_fast_cut(d)

    def test_percent_not_fraction(self):
        """口径必须是百分数：0.25% 的浮亏写成 0.0025 会永远切不到。"""
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.0025, peak_pnl_pct=0.001)
        assert not _is_fast_cut(d), "小数口径的输入不应被当成 -0.25%"


class TestRollback:
    def test_loss_pct_zero_restores_old_behavior(self, monkeypatch):
        """LOSS_PCT=0 → 回到"无浮盈即砍"，保证可一键回退。"""
        monkeypatch.setenv("SCALP_EXIT_FAST_CUT_LOSS_PCT", "0")
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=0.1, peak_pnl_pct=0.2)
        assert _is_fast_cut(d)

    def test_disabled_switch_still_works(self, monkeypatch):
        monkeypatch.setenv("SCALP_EXIT_FAST_CUT_ENABLED", "false")
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.5, peak_pnl_pct=0.1)
        assert not _is_fast_cut(d)

    def test_window_configurable(self, monkeypatch):
        monkeypatch.setenv("SCALP_EXIT_FAST_CUT_MIN", "60")
        d = _eval(hold_seconds=45 * 60, unrealized_pnl_pct=-0.5, peak_pnl_pct=0.1)
        assert not _is_fast_cut(d), "45min < 60min 窗口，不应触发"
        d2 = _eval(hold_seconds=61 * 60, unrealized_pnl_pct=-0.5, peak_pnl_pct=0.1)
        assert _is_fast_cut(d2)

    def test_malformed_loss_pct_falls_back(self, monkeypatch):
        """非法值不得让整条出场逻辑抛异常。"""
        monkeypatch.setenv("SCALP_EXIT_FAST_CUT_LOSS_PCT", "abc")
        d = _eval(hold_seconds=31 * 60, unrealized_pnl_pct=-0.5, peak_pnl_pct=0.1)
        assert d is None or isinstance(d.action, str)


class TestShortSideSymmetry:
    """空头的 unrealized_pnl_pct 已是方向化收益，判定逻辑与多头同构。"""

    def test_short_gone_bad_is_cut(self):
        d = _eval(side="short", hold_seconds=31 * 60,
                  unrealized_pnl_pct=-0.35, peak_pnl_pct=0.1)
        assert _is_fast_cut(d)

    def test_short_flat_is_spared(self):
        d = _eval(side="short", hold_seconds=31 * 60,
                  unrealized_pnl_pct=-0.05, peak_pnl_pct=0.1)
        assert not _is_fast_cut(d)
