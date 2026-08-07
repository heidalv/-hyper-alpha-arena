"""Unit tests for SL vs liquidation safety logic (2026-04-22 事故修复).

覆盖场景：
1. _ensure_sl_inside_liq: short 仓 SL 紧贴/越过 liq 时被自动下压
2. _ensure_sl_inside_liq: long 仓 SL 紧贴/越过 liq 时被自动上抬
3. _ensure_sl_inside_liq: SL 已经安全的情况下不被修改
4. _ensure_sl_inside_liq: liq 或 sl 缺失时安全退出
"""

from types import SimpleNamespace

import pytest

from backend.services.paper_trading_engine import PaperTradingEngine


def _make_pos(side, entry, sl, liq):
    return SimpleNamespace(
        symbol="TEST",
        side=side,
        entry_price=entry,
        sl_price=sl,
        liquidation_price=liq,
    )


class TestEnsureSlInsideLiq:
    def test_short_sl_above_liq_should_be_pushed_down(self):
        """short 仓 sl=2411.75, liq=2411.76（贴脸）→ sl 应被下压到 liq - 0.5%×entry"""
        pos = _make_pos("short", entry=2307.9, sl=2411.75, liq=2411.76)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        expected = round(2411.76 - 2307.9 * 0.005, 6)
        assert pos.sl_price == expected
        assert pos.sl_price < pos.liquidation_price

    def test_short_sl_way_above_liq_should_be_clamped(self):
        """short 仓 sl > liq 较多时仍被拉回到 liq - buffer"""
        pos = _make_pos("short", entry=2000.0, sl=2100.0, liq=2080.0)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        expected = round(2080.0 - 2000.0 * 0.005, 6)
        assert pos.sl_price == expected

    def test_short_sl_safely_below_liq_untouched(self):
        """short 仓 sl 已远在 liq 下方 → 不应被修改"""
        pos = _make_pos("short", entry=2000.0, sl=2050.0, liq=2080.0)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        assert pos.sl_price == 2050.0

    def test_long_sl_below_liq_should_be_pushed_up(self):
        """long 仓 sl=1900, liq=1950（entry=2000）→ sl 应被拉到 liq + buffer"""
        pos = _make_pos("long", entry=2000.0, sl=1900.0, liq=1950.0)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        expected = round(1950.0 + 2000.0 * 0.005, 6)
        assert pos.sl_price == expected
        assert pos.sl_price > pos.liquidation_price

    def test_long_sl_safely_above_liq_untouched(self):
        """long 仓 sl 已远在 liq 上方 → 不应被修改"""
        pos = _make_pos("long", entry=2000.0, sl=1980.0, liq=1900.0)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        assert pos.sl_price == 1980.0

    def test_missing_sl_noop(self):
        pos = _make_pos("long", entry=2000.0, sl=None, liq=1900.0)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        assert pos.sl_price is None

    def test_missing_liq_noop(self):
        pos = _make_pos("long", entry=2000.0, sl=1980.0, liq=None)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        assert pos.sl_price == 1980.0

    def test_custom_safety_margin(self):
        """自定义 safety_margin 生效"""
        pos = _make_pos("short", entry=2000.0, sl=2050.0, liq=2040.0)
        PaperTradingEngine._ensure_sl_inside_liq(pos, safety_margin=0.02)  # 2%
        expected = round(2040.0 - 2000.0 * 0.02, 6)
        assert pos.sl_price == expected


class TestBugReproEth20xShort:
    """重现 2026-04-22 ETH 20x short 爆仓场景，验证修复后 SL 会先于 liq 触发."""

    def test_eth_20x_short_sl_is_adjusted_safely_inside_liq(self):
        # ETH entry=2307.9, 20x short, AI sl=2411.75, liq=2411.76
        pos = _make_pos("short", entry=2307.9, sl=2411.75, liq=2411.76)
        PaperTradingEngine._ensure_sl_inside_liq(pos)
        # sl 应被拉到比 liq 低至少 0.5%×entry
        assert pos.sl_price < pos.liquidation_price
        assert (pos.liquidation_price - pos.sl_price) >= pos.entry_price * 0.005 - 1e-6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
