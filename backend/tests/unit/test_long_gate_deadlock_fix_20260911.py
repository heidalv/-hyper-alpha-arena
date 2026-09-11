# -*- coding: utf-8 -*-
"""长边两闸互斥修复 + trend_broken 价格闸 契约测试（2026-09-11）。

背景（V8/V9/V11/V14/V17，`_audit_ml/`）：
  1. **两闸互斥**：mid 层 + 日线 chop + RegimeAgent=ranging 时，
     多头闸（learned/chop）要求 pos≥60%，位置闸拒 pos≥60% ⇒ 无单可开（合成证明 V17）；
     第十八轮标定的 chop 最优档（pos≥60&chg≥2 → 多头 +1.722%/胜率 0.857）永不执行。
  2. **trend_broken 砍在噪音区**：long 组 24 笔实际均 −4.10（合计 −98.46），
     同批入场纯 ExitPolicy 反事实 +8.127%/笔（胜率 83%）⇒ 浮亏 <3%（SL6% 的一半）
     且日线非 down 时不该由 4h 复查砍仓。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import backend.services.full_auto.midlong_circuit_gate as cg  # noqa: E402
from backend.services.full_auto.midlong_location_gate import location_gate_check  # noqa: E402
from backend.services.full_auto.midlong_position_manager import trend_broken_price_gate  # noqa: E402


def _ms(price=104.0, hi=110.0, lo=90.0, chg24=1.0):
    """pos = (104-90)/(110-90) = 70%"""
    return {"BTC": {"symbol": "BTC", "price": price, "range_24h_high": hi,
                    "range_24h_low": lo, "price_change_24h_pct": chg24}}


@pytest.fixture
def chop_learned_mid(monkeypatch):
    """日线 chop + learned 多头闸 + mid 层受管辖。"""
    monkeypatch.setenv("MIDLONG_LONG_MODE", "learned")
    monkeypatch.setenv("MIDLONG_LONG_LEARNED_TIERS", "mid")
    monkeypatch.setattr(cg, "_daily_regime", lambda sym: "chop")
    return monkeypatch


class TestLocationGateDefer:
    def test_chop_mid_long_defers_to_long_gate(self, chop_learned_mid):
        """pos=70% 在 chop+mid 下不再被位置闸拒（让位给多头闸）。"""
        ok, why, detail = location_gate_check(
            "BTC", "buy", tier="mid", regime="ranging", market_summary=_ms())
        assert ok is True, why
        assert "让位" in why
        assert detail.get("defer_to_long_gate") is True

    def test_switch_off_restores_veto(self, chop_learned_mid):
        """回滚开关关闭 → 恢复旧行为（pos≥60% 拒多）。"""
        chop_learned_mid.setenv("MIDLONG_LOCATION_DEFER_TO_LONG_GATE", "false")
        ok, why, _ = location_gate_check(
            "BTC", "buy", tier="mid", regime="ranging", market_summary=_ms())
        assert ok is False and "location_gate_veto" in why

    def test_non_chop_still_vetoes(self, chop_learned_mid):
        """日线非 chop（up）→ 位置闸照旧拦。"""
        chop_learned_mid.setattr(cg, "_daily_regime", lambda sym: "up")
        ok, why, _ = location_gate_check(
            "BTC", "buy", tier="mid", regime="ranging", market_summary=_ms())
        assert ok is False and "location_gate_veto" in why

    def test_long_tier_not_covered_by_learned(self, chop_learned_mid):
        """long 层不受 learned 多头闸管辖（第二十一轮）→ 位置闸照旧拦。"""
        ok, why, _ = location_gate_check(
            "BTC", "buy", tier="long", regime="ranging", market_summary=_ms())
        assert ok is False and "location_gate_veto" in why

    def test_knife_catch_still_blocks_when_deferring(self, chop_learned_mid):
        """让位只跳过位置规则：24h 已跌 ≥5% 的接刀仍拦。"""
        ok, why, _ = location_gate_check(
            "BTC", "buy", tier="mid", regime="ranging",
            market_summary=_ms(chg24=-6.0))
        assert ok is False and "接刀" in why

    def test_short_side_unaffected(self, chop_learned_mid):
        """让位仅作用于买入；卖出规则不变。"""
        ok, why, _ = location_gate_check(
            "BTC", "sell", tier="mid", regime="ranging",
            market_summary=_ms(price=92.0))  # pos=10% → 低位追空应拒
        assert ok is False and "低位追空" in why

    def test_defer_source_contract(self):
        src = open(os.path.join(
            os.path.dirname(__file__), "..", "..", "services", "full_auto", "midlong_location_gate.py",
        ), encoding="utf-8").read()
        assert "_long_gate_authoritative" in src
        assert "MIDLONG_LOCATION_DEFER_TO_LONG_GATE" in src
        assert "and not _defer_pos" in src


class TestTrendBrokenPriceGate:
    """注：闸读 settings 属性（`_cfg_float` 约定），故运行期开关走 .env + 重启；
    测试直接 monkeypatch settings 属性（与生产生效路径一致）。"""

    def _pos(self, mark, entry=100.0, symbol="BTC", tier="mid"):
        return {"symbol": symbol, "entry_price": entry, "mark_price": mark,
                "timeframe_tier": tier}

    def _set_gate(self, monkeypatch, value):
        import backend.config.settings as settings_mod
        monkeypatch.setattr(settings_mod, "MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS", value, raising=False)

    def test_small_loss_blocked(self, monkeypatch):
        self._set_gate(monkeypatch, 3.0)
        monkeypatch.setattr(cg, "_daily_regime", lambda sym: "up")
        ok, why = trend_broken_price_gate(self._pos(99.0), side="long", tier="mid")
        assert ok is False and "噪音区不砍仓" in why

    def test_deep_loss_allowed(self, monkeypatch):
        self._set_gate(monkeypatch, 3.0)
        ok, why = trend_broken_price_gate(self._pos(96.0), side="long", tier="mid")
        assert ok is True and "放行" in why

    def test_down_regime_allowed(self, monkeypatch):
        self._set_gate(monkeypatch, 3.0)
        monkeypatch.setattr(cg, "_daily_regime", lambda sym: "down")
        ok, why = trend_broken_price_gate(self._pos(99.5), side="long", tier="mid")
        assert ok is True and "down" in why

    def test_threshold_zero_rollback(self, monkeypatch):
        self._set_gate(monkeypatch, 0.0)
        ok, why = trend_broken_price_gate(self._pos(99.9), side="long", tier="mid")
        assert ok is True and "关闭" in why

    def test_settings_key_declared(self):
        """配置必须落在 settings（否则 _cfg_float 读不到 —— 本项目反复出现的坑）。"""
        import backend.config.settings as settings_mod
        assert hasattr(settings_mod, "MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS")
        assert float(settings_mod.MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS) == pytest.approx(3.0)

    def test_short_side_price_direction(self, monkeypatch):
        self._set_gate(monkeypatch, 3.0)
        monkeypatch.setattr(cg, "_daily_regime", lambda sym: "up")
        ok, _ = trend_broken_price_gate(
            {"symbol": "BTC", "entry_price": 100.0, "mark_price": 101.0}, side="short", tier="long")
        assert ok is False, "空头浮亏按 (mark-entry)/entry 计算"
        ok2, _ = trend_broken_price_gate(
            {"symbol": "BTC", "entry_price": 100.0, "mark_price": 104.0}, side="short", tier="long")
        assert ok2 is True

    def test_missing_price_fail_open(self, monkeypatch):
        self._set_gate(monkeypatch, 3.0)
        ok, why = trend_broken_price_gate({"symbol": "BTC"}, side="long", tier="mid")
        assert ok is True and "fail-open" in why

    def test_wired_into_close_path(self):
        src = open(os.path.join(
            os.path.dirname(__file__), "..", "..", "services", "full_auto", "midlong_position_manager.py",
        ), encoding="utf-8").read()
        assert "trend_broken_price_gate(position, side=side, tier=pos_tier)" in src
        assert "trend_broken 价格闸拦截" in src
