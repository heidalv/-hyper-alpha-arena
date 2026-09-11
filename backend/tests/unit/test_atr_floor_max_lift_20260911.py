# -*- coding: utf-8 -*-
"""[2026-09-11] ATR 地板抬升上限契约测试。

现场：UNI 论题 SL 4.8%，ATR_1d≈10.8%×1.5=16.24% 地板 → TP=2×SL=32.5%
被 tier 上限钳到 20% → 净 RR=(20%-funding)/16.24%≈1.23 < MIDLONG_MIN_NET_RR(1.3)
→ funding 闸恒拦（19:11 日志实证）→ 入场链死锁。

契约：
- 默认：地板抬升不超过原 SL 的 2.0 倍（4.8%→9.6%，不是 16.24%）。
- 上限内不封顶，照旧返回全量地板。
- MAX_LIFT=0 回滚旧口径（无上限）。
- no_atr / floor≤sl 行为不变。

注：midlong_trade_design._cfg_float 读 settings 属性（导入时求值），
故本测试 monkeypatch settings 属性而非环境变量。
"""
import backend.config.settings as settings
from backend.services.mlto.midlong_trade_design import apply_structure_atr_floor


def _set_lift(monkeypatch, value):
    monkeypatch.setattr(settings, "MIDLONG_ATR_SL_MULT", 1.5, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_ATR_FLOOR_MAX_LIFT", value, raising=False)


def test_floor_capped_at_two_x(monkeypatch):
    _set_lift(monkeypatch, 2.0)
    sl, why = apply_structure_atr_floor(sl_pct=0.048, atr_1d_pct=0.108)
    assert sl == 0.096, why
    assert "封顶" in why


def test_floor_within_lift_not_capped(monkeypatch):
    _set_lift(monkeypatch, 2.0)
    # 地板 10.8%*1.5=16.2%；原 SL 10% → 16.2% ≤ 20% 不封顶
    sl, why = apply_structure_atr_floor(sl_pct=0.10, atr_1d_pct=0.108)
    assert abs(sl - 0.162) < 1e-9, why
    assert "封顶" not in why


def test_zero_lift_restores_old_behavior(monkeypatch):
    _set_lift(monkeypatch, 0.0)
    sl, why = apply_structure_atr_floor(sl_pct=0.048, atr_1d_pct=0.108)
    assert abs(sl - 0.162) < 1e-9, why


def test_no_atr_unchanged(monkeypatch):
    _set_lift(monkeypatch, 2.0)
    sl, why = apply_structure_atr_floor(sl_pct=0.05, atr_1d_pct=None)
    assert sl == 0.05 and why == "no_atr"


def test_floor_below_sl_unchanged(monkeypatch):
    _set_lift(monkeypatch, 2.0)
    sl, why = apply_structure_atr_floor(sl_pct=0.20, atr_1d_pct=0.10)
    assert sl == 0.20 and why == "ok"
