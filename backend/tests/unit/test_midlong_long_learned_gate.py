# -*- coding: utf-8 -*-
"""[2026-09-09 第十六轮引入，第十七/十八轮按实测修正] mid/long 多头
「learned 准入门」契约测试。

## 数据依据（全部本机数据库可复现）

1. 历史逐笔归因 `deep_long_attribution.py`（140 笔）：入场有边际（14d 持有
   +11.24%）、出场砍掉边际（出场差 -11.82%）→ 主修是出场（§9 已改），
   本门是入场侧加强。
2. 大样本条件验证 `test_long_entry_conditions.py` + `_audit_ml/W4_long_gate_design.py`
   （30 币 × 1h，n=53624，2025-07 起，72h 前/后段时间切分）。
3. **[第十七轮] hub 成交样本 72h 前向**（n=234，`deep_long_freshness.py` +
   W7/W8）：up 的 chg≥6 是 spike 追入（[6,10) -3.19% / ≥10% -5.05%）。
4. **[第十八轮] 近 30 天 184 笔实际 P&L**（`_audit_ml/X8_gate_variants_30d.py`）：
   up[3,6)+chop pos≥60&chg≥2 → **+0.406%/笔 胜率 0.536**（多头 +1.722%/0.857）；
   chop 无条件（第十七轮口径）→ -0.107%；up≥3 无上界 → +0.200%；不拦 → -0.406%。
   → up 加 spike 上界（第十七轮）+ chop 恢复位置动量条件（第十八轮回滚）。

## 契约

1. `learned`（默认）：down 拦；up 需 chg24∈[3,6)；chop 需 pos24≥60% 且 chg24≥2%；
2. 数据不足 / 特征读取异常 → **fail-open（放行）**（多头侧防过度阻止口径）；
3. 日线 regime 未知 → 放行（fail-open）；
4. 日内档（tier=short）豁免；
5. `regime_only`（回滚档）：仅 down 拦，up/chop 不查特征；
6. `allow_all`：完全不拦；
7. 阈值可配（`MIDLONG_LONG_CHG24_MIN_UP_PCT` / `MIDLONG_LONG_CHG24_MAX_UP_PCT` /
   `MIDLONG_LONG_CHG24_MIN_CHOP_PCT` / `MIDLONG_LONG_CHOP_POS_MIN_PCT`）。
"""
import importlib
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

MOD = "backend.services.full_auto.midlong_circuit_gate"


def _fresh(monkeypatch, long_mode="learned"):
    monkeypatch.setenv("MIDLONG_CIRCUIT_ENABLED", "true")
    # [2026-09-27] 总开关 true（撤销「不做空」封锁后的部署口径）；本文件只测多头分支，
    # 但显式钉住避免继承 .env 生产值造成漂移。
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "regime_gated")
    monkeypatch.setenv("MIDLONG_DOWN_SHORT_MODE", "flat")
    monkeypatch.setenv("MIDLONG_CHOP_MODE", "long_only")
    monkeypatch.setenv("MIDLONG_LONG_MODE", long_mode)
    # [M4 2026-09-14] 本文件契约测试锁的是 learned 门本身（hold 语义）；
    # paper 探针（缩仓放行）是独立政策，见 test_m4_gate_paper_probe_20260914.py。
    monkeypatch.setenv("MIDLONG_LEARNED_PAPER_PROBE", "false")
    # [2026-09-29 全面执行] 本文件契约锁的是 learned 门本身；新入场边际闸（edge_gate）
    # 会先于 learned 分支在真实行情下拦截 → 显式关闭隔离（同 test_midlong_circuit_gate）。
    monkeypatch.setenv("MIDLONG_EDGE_GATE_ENABLED", "false")
    monkeypatch.delenv("MIDLONG_LONG_CHG24_MIN_UP_PCT", raising=False)
    monkeypatch.delenv("MIDLONG_LONG_CHG24_MAX_UP_PCT", raising=False)
    monkeypatch.delenv("MIDLONG_LONG_CHG24_MIN_CHOP_PCT", raising=False)
    monkeypatch.delenv("MIDLONG_LONG_CHOP_POS_MIN_PCT", raising=False)
    monkeypatch.delenv("MIDLONG_LONG_LEARNED_TIERS", raising=False)
    m = importlib.import_module(MOD)
    m = importlib.reload(m)
    monkeypatch.setattr(m, "_STATE_FILE", os.path.join("data", "_test_circuit_state.json"))
    monkeypatch.setattr(m, "_state", {})
    monkeypatch.setattr(m, "_loaded", True)
    monkeypatch.setattr(m, "_LONG_FEAT_CACHE", {})
    return m


def _stub_regime(m, monkeypatch, mapping):
    monkeypatch.setattr(m, "_daily_regime", lambda sym: mapping.get(str(sym).upper(), ""))


def _stub_klines(m, monkeypatch, closes, highs=None, lows=None):
    """注入 fake kline_service；close 序列构造 1h K 线（high/low 默认 ±0.1%）。"""
    if highs is None:
        highs = [c * 1.001 for c in closes]
    if lows is None:
        lows = [c * 0.999 for c in closes]

    class _K:
        @staticmethod
        def get_aggregated_klines(sym, period, count=30):
            return [{"close": c, "high": h, "low": l}
                    for c, h, l in zip(closes, highs, lows)]

    fake = types.ModuleType("backend.services.kline_data_service")
    fake.kline_service = _K()
    monkeypatch.setitem(sys.modules, "backend.services.kline_data_service", fake)
    monkeypatch.setattr(m, "_LONG_FEAT_CACHE", {})


def _series(chg24_pct, pos_pct=None):
    """30 根 1h K 线：窗口（6..29）high=101/low=99，中间价 100，末价按 pos 分位；
    closes[5] 按 chg24 反推（chg24 = closes[29]/closes[5]-1）。pos 与 chg 可独立控制。"""
    px = 100.0 if pos_pct is None else 99.0 + 2.0 * pos_pct / 100.0
    closes = [0.0] * 30
    for k in range(6, 29):
        closes[k] = 100.0
    closes[29] = px
    closes[5] = round(px / (1.0 + chg24_pct / 100.0), 6)
    assert abs((closes[29] / closes[5] - 1.0) * 100 - chg24_pct) < 0.01
    highs = [101.0] * 30
    lows = [99.0] * 30
    return closes, highs, lows


# ── 1. 门级契约 ──

def test_learned_down_blocks_long(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_regime_block" in reason


def test_learned_up_requires_chg24(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    # chg24=1%（<3%）→ 拦
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "learned_long_up_chg24_+1.0<+3.0"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_learned_block" in reason
    # chg24=4%（∈[3,6)）→ 放行
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (True, "learned_long_up_ok(chg+4.0∈[3,6))"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok, reason
    # [第十七轮] chg24=8%（≥6% spike）→ 拦
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "learned_long_up_chg24_+8.0≥+6.0(spike)"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_learned_block" in reason


def test_learned_chop_requires_pos_and_chg(monkeypatch):
    """[第十八轮回滚] chop 需 pos24≥60% 且 chg24≥2%：近 30 天实际 P&L 显示
    chop 无条件放行 -0.107%/笔（n=80），加回该条件后 +0.406%/笔（n=28）。"""
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "chop"})
    # pos 或 chg 不满足 → 拦
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "learned_long_chop_pos40<60"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_learned_block" in reason
    # 满足 → 放行
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (True, "learned_long_chop_ok(pos70,chg+3.0)"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok, reason


def test_learned_skips_long_tier(monkeypatch):
    """[2026-09-10 第二十一轮] learned 特征闸默认只作用 mid 层。

    依据（`_audit_ml/Y26_long_gate_impact.py`）：long 层被拦 18 笔净 +$55.48
    （14 赢家 +$143.07），放行 9 笔净 -$0.07 —— 动量门是 mid 标定的，
    用在 Chandelier 趋势车道会拦掉大部分利润。
    """
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "should_not_be_consulted"))
    # tier=long → 不查特征闸，放行
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="long")
    assert ok, reason
    # tier=mid → 仍拦
    ok2, reason2 = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok2 and "midlong_long_learned_block" in reason2
    # 显式扩展车道 → long 也查（注意：_fresh 会清该键，必须在 reload 之后再设）
    m2 = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_LONG_LEARNED_TIERS", "mid,long")
    _stub_regime(m2, monkeypatch, {"BTC": "up"})
    monkeypatch.setattr(m2, "_long_learned_ok", lambda sym, reg: (False, "learned_long_up_chg24_+1.0<+3.0"))
    ok3, reason3 = m2.check_midlong_entry(14, "BTC", side="buy", tier="long")
    assert not ok3 and "midlong_long_learned_block" in reason3


def test_learned_unknown_regime_fail_open(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {})  # 日线 regime 不可判
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "should_not_be_consulted"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok, reason


def test_regime_only_rollback_skips_features(monkeypatch):
    m = _fresh(monkeypatch, long_mode="regime_only")
    _stub_regime(m, monkeypatch, {"BTC": "up"})
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "should_not_be_consulted"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok, reason
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_regime_block" in reason


def test_allow_all(monkeypatch):
    m = _fresh(monkeypatch, long_mode="allow_all")
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert ok, reason


def test_intraday_tier_exempt(monkeypatch):
    m = _fresh(monkeypatch)
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    monkeypatch.setattr(m, "_long_learned_ok", lambda sym, reg: (False, "should_not_be_consulted"))
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="short")
    assert ok, reason


def test_long_mode_independent_of_short_mode(monkeypatch):
    """第十六轮语义：多头治理不再受 MIDLONG_SHORT_MODE 牵制。"""
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "off")
    _stub_regime(m, monkeypatch, {"BTC": "down"})
    ok, reason = m.check_midlong_entry(14, "BTC", side="buy", tier="mid")
    assert not ok and "midlong_long_regime_block" in reason


# ── 2. 特征函数级契约（真实 chg24 计算）──

def test_learned_ok_up_chg24(monkeypatch):
    m = _fresh(monkeypatch)
    closes, highs, lows = _series(chg24_pct=1.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "up")
    assert not ok and "chg24" in why, why
    closes, highs, lows = _series(chg24_pct=4.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "up")
    assert ok, why
    # [第十七轮] spike 上界：chg24=8%（≥6%）→ 拦
    closes, highs, lows = _series(chg24_pct=8.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "up")
    assert not ok and "spike" in why, why


def test_learned_ok_up_threshold_env(monkeypatch):
    m = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_LONG_CHG24_MIN_UP_PCT", "5.0")
    closes, highs, lows = _series(chg24_pct=4.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "up")
    assert not ok and "chg24" in why, why
    # [第十七轮] 上界阈值可配：MAX=5 → chg24=5.5 拦
    m2 = _fresh(monkeypatch)
    monkeypatch.setenv("MIDLONG_LONG_CHG24_MAX_UP_PCT", "5.0")
    closes, highs, lows = _series(chg24_pct=5.5)
    _stub_klines(m2, monkeypatch, closes, highs, lows)
    ok2, why2 = m2._long_learned_ok("BTC", "up")
    assert not ok2 and "spike" in why2, why2


def test_learned_ok_chop_pos_and_chg(monkeypatch):
    """[第十八轮回滚] chop 位置+动量条件（真实特征计算）。"""
    m = _fresh(monkeypatch)
    # chg 满足（+3%）但 pos=50%<60% → 拦
    closes, highs, lows = _series(chg24_pct=3.0, pos_pct=50.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "chop")
    assert not ok and "pos" in why, why
    # pos=70% + chg=+3% → 放行
    closes, highs, lows = _series(chg24_pct=3.0, pos_pct=70.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "chop")
    assert ok, why
    # pos=70% + chg=+1%（<2%）→ 拦
    closes, highs, lows = _series(chg24_pct=1.0, pos_pct=70.0)
    _stub_klines(m, monkeypatch, closes, highs, lows)
    ok, why = m._long_learned_ok("BTC", "chop")
    assert not ok and "chg24" in why, why


def test_learned_ok_nodata_fail_open(monkeypatch):
    m = _fresh(monkeypatch)

    class _K:
        @staticmethod
        def get_aggregated_klines(sym, period, count=30):
            return []  # 数据不足

    fake = types.ModuleType("backend.services.kline_data_service")
    fake.kline_service = _K()
    monkeypatch.setitem(sys.modules, "backend.services.kline_data_service", fake)
    monkeypatch.setattr(m, "_LONG_FEAT_CACHE", {})
    ok, why = m._long_learned_ok("BTC", "up")
    assert ok and "fail-open" in why, why


def test_learned_ok_exception_fail_open(monkeypatch):
    m = _fresh(monkeypatch)

    class _K:
        @staticmethod
        def get_aggregated_klines(sym, period, count=30):
            raise RuntimeError("kline db down")

    fake = types.ModuleType("backend.services.kline_data_service")
    fake.kline_service = _K()
    monkeypatch.setitem(sys.modules, "backend.services.kline_data_service", fake)
    monkeypatch.setattr(m, "_LONG_FEAT_CACHE", {})
    ok, why = m._long_learned_ok("BTC", "up")
    assert ok and "failopen" in why, why


def test_learned_ok_cache(monkeypatch):
    """缓存 15 分钟内不重复拉 K 线。"""
    m = _fresh(monkeypatch)
    calls = {"n": 0}

    class _K:
        @staticmethod
        def get_aggregated_klines(sym, period, count=30):
            calls["n"] += 1
            closes, highs, lows = _series(chg24_pct=4.0)
            return [{"close": c, "high": h, "low": l}
                    for c, h, l in zip(closes, highs, lows)]

    fake = types.ModuleType("backend.services.kline_data_service")
    fake.kline_service = _K()
    monkeypatch.setitem(sys.modules, "backend.services.kline_data_service", fake)
    monkeypatch.setattr(m, "_LONG_FEAT_CACHE", {})
    ok1, _ = m._long_learned_ok("BTC", "up")
    ok2, _ = m._long_learned_ok("BTC", "up")
    assert ok1 and ok2
    assert calls["n"] == 1
