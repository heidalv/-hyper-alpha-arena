# -*- coding: utf-8 -*-
"""[2026-09-09 二次修订] mid/long 空头「日线 regime 门」契约测试。

## 数据依据

`_audit_ml/N3_regime.py`（29 币日线，EMA200 + 60 日动量分档）：

| regime | 方向 | n | 均值% | 胜率 | t |
|---|---|---|---|---|---|
| up | long-14d | 4134 | +1.038 | 0.468 | +4.11 |
| up | short-14d | 4134 | -1.038 | 0.532 | -4.11 |
| chop | long-14d | 5192 | +0.138 | 0.447 | +0.61 |
| down | long-14d | 8899 | -0.618 | 0.447 | -4.09 |
| down | short-14d | 8899 | +0.618 | 0.542 | +4.09 |

`_audit_ml/N5_variants.py`（281 笔真实 mid/long 平仓）：
全停（只做多）-65.51 / 旧 conditional -127.57 / **regime 门 -44.19**。

## 契约

1. `regime_gated`（默认）下：**只有 down regime 允许开空**；up/chop 拦；
2. 同一 regime 门对称生效：**down regime 禁止开多**（down-long t=-4.09）；
3. 日线数据不可判 → 空头 fail-closed（不放行），多头 fail-open；
4. 日内档（tier=short）豁免，不受本门限制；
5. `off` / `conditional` / `on` 三个回滚档行为不变。
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

MOD = "backend.services.full_auto.midlong_circuit_gate"


def _fresh(monkeypatch, mode="regime_gated"):
    monkeypatch.setenv("MIDLONG_CIRCUIT_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "false")
    monkeypatch.setenv("MIDLONG_SHORT_MODE", mode)
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "flat")  # 显式声明，避免环境漂移（生产默认 learned）
    monkeypatch.setenv("MIDLONG_CHOP_MODE", "long_only")
    # [2026-09-09 第十六轮] 多头治理已独立（MIDLONG_LONG_MODE，生产默认 learned）；
    # 本文件契约基于「仅 down 拦」的多头口径，显式钉 regime_only（learned 分支
    # 契约见 test_midlong_long_learned_gate.py）。
    monkeypatch.setenv("MIDLONG_LONG_MODE", "regime_only")
    m = importlib.import_module(MOD)
    m = importlib.reload(m)
    monkeypatch.setattr(m, "_STATE_FILE", os.path.join("data", "_test_circuit_state.json"))
    monkeypatch.setattr(m, "_state", {})
    monkeypatch.setattr(m, "_loaded", True)
    return m


def _stub_regime(m, monkeypatch, mapping):
    monkeypatch.setattr(m, "_daily_regime", lambda sym: mapping.get(str(sym).upper(), ""))


def test_down_regime_allows_short(monkeypatch):
    """[2026-09-09 第十四轮] down-regime 空头默认 learned 准入；flat 全关；allowed 无条件。"""
    # 默认 learned：分位/RSI 不满足 → 拦
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "learned")
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    monkeypatch.setattr(m, "_short_learned_ok", lambda sym: (False, "test_pos40<60"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_learned_block" in reason
    # learned 满足 → 放行
    monkeypatch.setattr(m, "_short_learned_ok", lambda sym: (True, "test_ok"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok, reason
    # flat：全关
    m2 = _fresh(monkeypatch)
    _stub_regime(m2, monkeypatch, {"BTC": "down"})
    ok, reason = m2.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_down_flat" in reason
    # allowed：无条件放行
    monkeypatch.setattr(m2, "_down_short_enabled", lambda: "allowed")
    ok, reason = m2.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok, reason


def test_up_regime_blocks_short(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_regime_block" in reason


def test_chop_regime_blocks_short(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "chop"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_regime_block" in reason


def test_unknown_regime_short_fail_closed(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {})
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_regime_unknown" in reason


def test_down_regime_blocks_long(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_regime_block" in reason


def test_up_regime_allows_long(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok, reason


def test_intraday_tier_exempt(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="short")
    assert ok, reason


def test_off_mode_blocks_all_shorts(monkeypatch):
    m = _fresh(monkeypatch, mode="off")
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_off" in reason
    # [2026-09-09 第十六轮] 多头治理独立于空头开关：off 模式下 down-long
    # 仍被 MIDLONG_LONG_MODE=regime_only 拦（down-long t=-4.09）；up-long 放行。
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_regime_block" in reason
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    ok, _ = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok


def test_conditional_mode_legacy(monkeypatch):
    m = _fresh(monkeypatch, mode="conditional")
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    # conditional 不看 regime，只看 4h/24h 下行证据
    ok, reason = m.check_midlong_entry(14, "BTC", side="sell", tier="mid",
                                       market_summary={"BTC": {}})
    assert not ok and "midlong_short_no_bias" in reason
    ok, _ = m.check_midlong_entry(14, "BTC", side="sell", tier="mid",
                                  market_summary={"BTC": {"price_change_24h_pct": -0.03}})
    assert ok


def test_on_mode_unconditional(monkeypatch):
    m = _fresh(monkeypatch, mode="on")
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    ok, _ = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok


# ── chop 空仓开关（第十轮，2026-09-09）──
# 依据 `_audit_ml/P1_chop.py`：chop 期间 85 笔合计 -53.66（多头 -14.05 / 空头 -39.60）；
# 方案 F（chop 空仓）-30.14/136 笔 vs 方案 C（chop 只多）-44.19/175 笔。

def test_chop_mode_default_long_only(monkeypatch):
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_CHOP_MODE", "long_only")
    _stub_regime(m, monkeypatch, {"BTC": "chop"})
    ok_long, _ = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    ok_short, _ = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok_long, "默认 long_only 下 chop 应允许做多"
    assert not ok_short


def test_chop_flat_blocks_both(monkeypatch):
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_CHOP_MODE", "flat")
    _stub_regime(m, monkeypatch, {"BTC": "chop"})
    ok_long, r_long = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    ok_short, _ = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok_long and "midlong_chop_flat" in r_long
    assert not ok_short


def test_chop_flat_does_not_affect_trend_regimes(monkeypatch):
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_CHOP_MODE", "flat")
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    ok_long, _ = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok_long, "chop flat 不应影响 up regime 的做多"
    # [2026-09-09 第十三轮] down 空头默认 flat（关闭）；显式 allowed 才放行
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "allowed")
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok_short, _ = m.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok_short, "chop flat 不应影响 down regime 的做空（allowed 时）"


# ── [2026-09-09 第十五轮] learned 空头准入三条件（分位≥60 + RSI∈[55,80] + chg24≤-1%）──
# 依据 `test_short_entry_conditions.py`（n=31684 时间切分）：原两条件后段 -0.74%/72h；
# 加 chg24≤-1% 后前段 +1.01%/后段 +1.07%，唯一两段皆正。

def _klines_30(prices):
    """由 close 序列构造 30 根 1h K 线 dict（high/low 用 close±0.1%）。"""
    return [{"close": p, "high": p * 1.001, "low": p * 0.999} for p in prices]


def _stub_klines(m, monkeypatch, prices):
    import types

    class _K:
        @staticmethod
        def get_aggregated_klines(sym, period, count=30):
            return _klines_30(prices)

    fake = types.ModuleType("backend.services.kline_data_service")
    fake.kline_service = _K()
    monkeypatch.setitem(sys.modules, "backend.services.kline_data_service", fake)
    monkeypatch.setattr(m, "_SHORT_FEAT_CACHE", {})


def _chg_series(chg24_pct):
    """构造 30 根 close：窗口（6..29）内 101 尖峰 + 98 低点，末价 100（分位≈67%）；
    最后 15 根 9 涨 5 跌（RSI≈64）；closes[5] 按 chg24 反推。"""
    closes = [0.0] * 30
    closes[5] = round(100.0 / (1.0 + chg24_pct / 100.0), 2)
    for k in range(6, 14):
        closes[k] = 101.0
    closes[14] = 98.0
    tail = [99.2, 99.4, 99.6, 99.8, 100.0, 100.2, 100.4, 100.6, 100.8, 101.0,
            100.8, 100.6, 100.4, 100.2, 100.0]
    for k, v in enumerate(tail):
        closes[15 + k] = v
    return closes


def test_learned_short_requires_chg24_not_chasing(monkeypatch):
    """chg24>=-1%（追涨）时拒开；≤-1% 且分位/RSI 满足时放行。"""
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "learned")
    _stub_klines(m, monkeypatch, _chg_series(+2.0))
    ok, why = m._short_learned_ok("BTC")
    assert not ok and "chg24" in why, why
    _stub_klines(m, monkeypatch, _chg_series(-2.0))
    ok, why = m._short_learned_ok("BTC")
    assert ok, why


def test_learned_short_pos_and_rsi_still_enforced(monkeypatch):
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "learned")
    # 窗口全平（hi==lo → 分位 50<60）→ 拒
    flat = [100.0] * 30
    flat[5] = 102.04  # chg24 = -2%（仅此一根在窗口外）
    _stub_klines(m, monkeypatch, flat)
    ok, why = m._short_learned_ok("BTC")
    assert not ok and ("pos" in why or "rsi" in why), why
