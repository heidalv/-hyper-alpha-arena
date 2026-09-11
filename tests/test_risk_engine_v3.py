# -*- coding: utf-8 -*-
"""v3 方向 7：RiskEngine 单入口 / TradingState / LIVE_KILL_SWITCH / 熔断 / 日配额 停机单测。

全部纯逻辑，不连数据库：
- TradingState 三态、TTL 过期回收、escalate_only 不降级、HALTED 不过期、持久化重载
- LIVE_KILL_SWITCH 三入口（env / 文件 / API engage）、scope=live 只拦实盘、release
- 连通性熔断：连续失败 ≥ 阈值跳闸、成功复位、冷却放行
- 闪崩纯函数：1h/4h 阈值、数据不足、上涨不触发
- 组合回撤分级：<20% 全仓、20~30% 减半、≥30% 只平不开
- 日配额：桶归类、cap 单一来源、用尽拦截、平仓永远放行
- pre_trade 顺序：kill → state → connectivity → window → quota；disabled 模式只观察
- 飞书命令解析：/kill /release /reduce /state
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """把状态文件与 KILL 文件指到临时目录，并重置单例。"""
    from backend.services.risk import trading_state as ts
    from backend.services.risk import kill_switch as ks
    from backend.services.risk import risk_engine as re_mod
    from backend.services.risk import circuit_breakers as cb

    store = ts.TradingStateStore(path=str(tmp_path / "trading_state.json"))
    monkeypatch.setattr(ts, "_store", store)
    monkeypatch.setattr(ks, "KILL_FILE", str(tmp_path / "KILL_SWITCH"))
    monkeypatch.setattr(ks, "RISK_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(re_mod, "_engine", None)
    monkeypatch.setattr(cb, "_connectivity", None)
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    monkeypatch.delenv("LIVE_KILL_SWITCH_SCOPE", raising=False)
    monkeypatch.delenv("RISK_ENGINE_V3_ENABLED", raising=False)
    # 告警走飞书：单测里打桩
    monkeypatch.setattr(ks, "_alert", lambda *a, **k: None)
    monkeypatch.setattr(cb, "_alert_connectivity", lambda *a, **k: None)
    monkeypatch.setattr(re_mod.RiskEngine, "_alert", staticmethod(lambda *a, **k: None))
    return store


# ── TradingState ────────────────────────────────────────────────────────────

def test_trading_state_ttl_expiry_and_persistence(isolated_state):
    from backend.services.risk.trading_state import TradingState, TradingStateStore

    store = isolated_state
    assert store.state() == TradingState.ACTIVE
    store.set_state(TradingState.REDUCING, reason="flash", source="flash_crash", ttl_seconds=0.2)
    assert store.state() == TradingState.REDUCING
    # 持久化：新实例读同一文件
    other = TradingStateStore(path=store._path)
    assert other.state() == TradingState.REDUCING
    time.sleep(0.25)
    store._loaded_at = 0.0  # 强制重读
    assert store.state() == TradingState.ACTIVE
    assert store.snapshot().source == "ttl"


def test_trading_state_halted_never_expires_and_escalate_only(isolated_state):
    from backend.services.risk.trading_state import TradingState

    store = isolated_state
    store.set_state(TradingState.HALTED, reason="manual", source="api", ttl_seconds=0.1)
    time.sleep(0.15)
    store._loaded_at = 0.0
    assert store.state() == TradingState.HALTED, "HALTED 不得因 TTL 自动过期"
    # 自动触发器不能把 HALTED 降级为 REDUCING
    store.set_state(TradingState.REDUCING, reason="dd", source="drawdown", ttl_seconds=60, escalate_only=True)
    assert store.state() == TradingState.HALTED
    # 人工可以降级
    store.set_state(TradingState.ACTIVE, reason="ok", source="api")
    assert store.state() == TradingState.ACTIVE
    assert store.snapshot().position_scale == 1.0


def test_no_open_window_expires(isolated_state):
    store = isolated_state
    store.add_no_open_window(until=time.time() + 0.2, reason="news", symbols=["btc"])
    assert store.in_no_open_window("BTC/USDT") is not None
    assert store.in_no_open_window("ETH") is None
    time.sleep(0.25)
    store._loaded_at = 0.0
    assert store.in_no_open_window("BTC") is None


# ── LIVE_KILL_SWITCH ───────────────────────────────────────────────────────

def test_kill_switch_three_entries(isolated_state, monkeypatch):
    from backend.services.risk import kill_switch as ks
    from backend.services.risk.trading_state import TradingState

    assert ks.kill_switch_status().engaged is False
    # 1. env
    monkeypatch.setenv("LIVE_KILL_SWITCH", "true")
    st = ks.kill_switch_status()
    assert st.engaged and st.source == "env"
    assert st.blocks(live=True) and not st.blocks(live=False)
    monkeypatch.setenv("LIVE_KILL_SWITCH_SCOPE", "all")
    assert ks.kill_switch_status().blocks(live=False)
    monkeypatch.delenv("LIVE_KILL_SWITCH")
    monkeypatch.delenv("LIVE_KILL_SWITCH_SCOPE")
    # 2. 文件
    with open(ks.KILL_FILE, "w", encoding="utf-8") as f:
        f.write("ops wrote this")
    st = ks.kill_switch_status()
    assert st.engaged and st.source == "file" and "ops wrote" in st.reason
    os.remove(ks.KILL_FILE)
    # 3. API engage → 文件 + HALTED
    st = ks.engage("test reason", source="api")
    assert st.engaged and st.source == "file"
    assert isolated_state.state() == TradingState.HALTED
    st = ks.release(source="api")
    assert st.engaged is False
    assert isolated_state.state() == TradingState.ACTIVE


# ── 连通性熔断 ─────────────────────────────────────────────────────────────

def test_connectivity_breaker_trip_reset_cooldown(monkeypatch):
    from backend.services.risk import circuit_breakers as cb_mod
    from backend.services.risk.circuit_breakers import ConnectivityBreaker

    monkeypatch.setattr(cb_mod, "_alert_connectivity", lambda *a, **k: None)
    cb = ConnectivityBreaker(threshold=3, cooldown_sec=0.5)
    cb.record_failure("binance", "timeout")
    cb.record_failure("binance", "timeout")
    assert cb.is_tripped("binance") is False
    cb.record_failure("binance", "timeout")
    assert cb.is_tripped("binance") is True
    assert cb.is_tripped("asterdex") is False
    assert cb.tripped_venues() == ["binance"]
    time.sleep(0.6)
    assert cb.is_tripped("binance") is False, "冷却期满应半开放行探测"
    cb.record_failure("binance", "again")
    assert cb.is_tripped("binance") is True
    cb.record_success("binance")
    assert cb.is_tripped("binance") is False
    assert cb.status()["venues"]["binance"]["consecutive_failures"] == 0


# ── 闪崩 / 回撤 纯函数 ─────────────────────────────────────────────────────

def test_flash_crash_pure_function():
    from backend.services.risk.circuit_breakers import flash_crash_check

    base = [100.0] * 5
    # 1h 跌 9%
    v = flash_crash_check(base + [91.0], th_1h=8, th_4h=12)
    assert v.triggered and "1h" in v.reason
    # 4h 累计跌 13%（每小时约 -3.4%）
    v = flash_crash_check([100, 96.6, 93.3, 90.2, 87.0], th_1h=8, th_4h=12)
    assert v.triggered and "4h" in v.reason
    # 小跌不触发
    assert flash_crash_check([100, 99, 98, 97.5, 97], th_1h=8, th_4h=12).triggered is False
    # 上涨不触发
    assert flash_crash_check([100, 105, 110, 115, 125], th_1h=8, th_4h=12).triggered is False
    # 数据不足
    assert flash_crash_check([100.0]).reason == "insufficient_data"


def test_drawdown_tiers():
    from backend.services.risk.circuit_breakers import drawdown_tier

    assert drawdown_tier(1000, 1000, halve_pct=20, reducing_pct=30).scale == 1.0
    t = drawdown_tier(790, 1000, halve_pct=20, reducing_pct=30)
    assert t.scale == 0.5 and t.reducing is False and t.label == "halved"
    t = drawdown_tier(690, 1000, halve_pct=20, reducing_pct=30)
    assert t.scale == 0.0 and t.reducing is True
    assert drawdown_tier(500, 0).label == "no_peak"


# ── 日配额 ────────────────────────────────────────────────────────────────

def test_daily_quota_bucket_and_check(monkeypatch):
    from backend.services.risk import daily_quota as dq

    assert dq.bucket_for("short", None) == "scalp"
    assert dq.bucket_for("mid", "scalp") == "scalp"
    assert dq.bucket_for("mid", "swing") == "trend"
    assert dq.bucket_for("long", "trend_follow") == "trend"
    assert dq.bucket_for(None, None) == "trend"

    caps = {"scalp": 20, "trend": 6, "total": 10, "live": 6}
    monkeypatch.setattr(dq, "cap_for", lambda b: caps[b])
    counts = {"scalp": 3, "trend": 6, "total": 9}
    monkeypatch.setattr(dq, "opens_today", lambda db, aid, bucket="total": counts[bucket])
    dq.invalidate_count_cache()

    v = dq.check(MagicMock(), 14, tier="mid", trade_nature="swing")
    assert v.allowed is False and v.bucket == "trend" and v.used == 6 and v.cap == 6
    v = dq.check(MagicMock(), 14, tier="short", trade_nature="scalp")
    assert v.allowed is True and v.bucket == "scalp"
    counts["total"] = 10
    dq.invalidate_count_cache()
    v = dq.check(MagicMock(), 14, tier="short", trade_nature="scalp")
    assert v.allowed is False and v.bucket == "total"
    # 0 = 不限制
    caps["trend"] = 0
    counts["total"] = 1
    dq.invalidate_count_cache()
    assert dq.check(MagicMock(), 14, tier="mid", trade_nature="swing").allowed is True


# ── pre_trade 顺序与语义 ───────────────────────────────────────────────────

def _req(**kw):
    from backend.services.risk.risk_engine import PreTradeRequest
    base = dict(account_id=14, symbol="BTC", side="buy", is_open=True, venue="paper",
                tier="mid", trade_nature="swing", notional=100.0, equity=5000.0, leverage=2.0)
    base.update(kw)
    return PreTradeRequest(**base)


def test_pre_trade_reduce_always_allowed(isolated_state, monkeypatch):
    from backend.services.risk import kill_switch as ks
    from backend.services.risk.risk_engine import get_risk_engine_v3
    from backend.services.risk.trading_state import TradingState

    isolated_state.set_state(TradingState.HALTED, reason="x", source="api")
    monkeypatch.setenv("LIVE_KILL_SWITCH", "true")
    monkeypatch.setenv("LIVE_KILL_SWITCH_SCOPE", "all")
    v = get_risk_engine_v3().pre_trade(None, _req(is_open=False, venue="binance"))
    assert v.allowed is True and v.checks[0]["check"] == "reduce_bypass"


def test_pre_trade_kill_switch_scope(isolated_state, monkeypatch):
    from backend.services.risk.risk_engine import get_risk_engine_v3

    monkeypatch.setenv("LIVE_KILL_SWITCH", "true")
    eng = get_risk_engine_v3()
    assert eng.pre_trade(None, _req(venue="binance")).reason_code == "kill_switch"
    assert eng.pre_trade(None, _req(venue="paper")).allowed is True, "scope=live 时 paper 不受急停影响"
    monkeypatch.setenv("LIVE_KILL_SWITCH_SCOPE", "all")
    assert eng.pre_trade(None, _req(venue="paper")).reason_code == "kill_switch"
    assert eng.counters["blocked"] == 2


def test_pre_trade_state_connectivity_window(isolated_state):
    from backend.services.risk.risk_engine import get_risk_engine_v3
    from backend.services.risk.trading_state import TradingState
    from backend.services.risk.circuit_breakers import get_connectivity_breaker

    eng = get_risk_engine_v3()
    isolated_state.set_state(TradingState.REDUCING, reason="dd", source="drawdown", ttl_seconds=60)
    v = eng.pre_trade(None, _req())
    assert v.allowed is False and v.reason_code == "trading_state_reducing"
    isolated_state.set_state(TradingState.ACTIVE, reason="ok", source="api")

    cb = get_connectivity_breaker()
    for _ in range(cb.threshold):
        cb.record_failure("asterdex", "timeout")
    assert eng.pre_trade(None, _req(venue="asterdex")).reason_code == "connectivity_tripped"
    assert eng.pre_trade(None, _req(venue="binance")).allowed is True
    cb.reset()

    isolated_state.add_no_open_window(until=time.time() + 60, reason="macro", symbols=["BTC"])
    assert eng.pre_trade(None, _req(symbol="BTC")).reason_code == "risk_window"
    assert eng.pre_trade(None, _req(symbol="ETH")).allowed is True


def test_pre_trade_quota_and_disabled_mode(isolated_state, monkeypatch):
    from backend.services.risk import daily_quota as dq
    from backend.services.risk.risk_engine import get_risk_engine_v3

    monkeypatch.setattr(dq, "check", lambda db, aid, **kw: dq.QuotaVerdict(False, "trend", 6, 6, "daily_quota[trend] 6/6 已用尽"))
    eng = get_risk_engine_v3()
    v = eng.pre_trade(MagicMock(), _req())
    assert v.allowed is False and v.reason_code == "daily_quota"
    # 观察模式：只记录不拦截
    monkeypatch.setenv("RISK_ENGINE_V3_ENABLED", "false")
    v = eng.pre_trade(MagicMock(), _req())
    assert v.allowed is True and v.reason_code == "observed:daily_quota"


def test_pre_trade_block_result_shape(isolated_state, monkeypatch):
    from backend.services.risk.risk_engine import get_risk_engine_v3
    monkeypatch.setenv("LIVE_KILL_SWITCH", "true")
    v = get_risk_engine_v3().pre_trade(None, _req(venue="binance"))
    r = v.as_block_result()
    assert r["success"] is False and r["blocked"] is True and r["blocked_layer"] == "risk_engine"
    assert r["reason_code"] == "kill_switch" and r["risk_engine"]["allowed"] is False


def test_tick_flash_crash_sets_reducing(isolated_state, monkeypatch):
    from backend.services.risk import risk_engine as re_mod
    from backend.services.risk.circuit_breakers import flash_crash_check
    from backend.services.risk.trading_state import TradingState

    monkeypatch.setattr(re_mod, "evaluate_flash_crash", lambda: flash_crash_check([100] * 5 + [90], th_1h=8, th_4h=12))
    monkeypatch.setattr(re_mod.RiskEngine, "_tick_drawdown", lambda self, store: {})
    out = re_mod.get_risk_engine_v3().tick()
    assert out["flash_crash"]["triggered"] is True
    assert isolated_state.state() == TradingState.REDUCING
    assert isolated_state.snapshot().source == "flash_crash"
    assert isolated_state.snapshot().until is not None


# ── 飞书 / 机器人命令 ──────────────────────────────────────────────────────

def test_ops_command_parser_and_run(isolated_state, monkeypatch):
    from backend.api.ops_risk_routes import _extract_command, run_command
    from backend.services.risk.trading_state import TradingState

    text, chat, token = _extract_command({
        "header": {"event_type": "im.message.receive_v1", "token": "t0k"},
        "event": {"message": {"chat_id": "oc_1", "content": "{\"text\":\"@_user_1 /kill 演练\"}"}},
    })
    assert text == "/kill 演练" and chat == "oc_1" and token == "t0k"
    assert _extract_command({"command": "/state"})[0] == "/state"

    r = run_command("/kill 演练", source="feishu")
    assert r["ok"] and isolated_state.state() == TradingState.HALTED
    r = run_command("/active", source="feishu")
    assert r["ok"] is False, "急停未释放不得 /active"
    r = run_command("/release", source="feishu")
    assert r["ok"] and isolated_state.state() == TradingState.ACTIVE
    r = run_command("/reduce 2", source="feishu")
    assert r["ok"] and isolated_state.state() == TradingState.REDUCING
    r = run_command("/state")
    assert r["ok"] and "reducing" in r["reply"]
    assert run_command("/nonsense")["ok"] is False
