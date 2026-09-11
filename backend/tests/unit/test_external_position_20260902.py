"""D12 外部手工持仓识别测试（2026-09-02 因子系统闭环修复）。

原病症：账户 188 的 VIRTUAL / XPL 为手工开仓，本地账本无对应子仓，对账连续
86 轮 ERROR + CRITICAL 刷屏。而自动对齐对这种情况本就只会返回 no_local_subs
（拒绝臆造），告警既无法处置也无人处置，真正需要关注的账本漂移被噪音淹没。

本测试锁定：手工仓要被识别并摘出 MISMATCH、日志只在数量变化时打印、真实漂移
（本地有账本但数量不符）绝不能被顺手放过。
"""
from __future__ import annotations

import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services import live_position_reconciler as lpr


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """把对账状态文件重定向到 tmp，避免污染真实运行状态。"""
    p = tmp_path / "live_reconcile_state.json"
    monkeypatch.setattr(lpr, "_STATE_PATH", str(p))
    monkeypatch.delenv("LIVE_RECONCILE_EXTERNAL_SYMBOLS", raising=False)
    return p


class _FakeLPM:
    """按预设的 (local, exchange) 返回对账结果。"""

    def __init__(self, table):
        self.table = table
        self.log_mismatch_seen = []

    def reconcile(self, db, account_id, symbol, qty, lev, *, log_mismatch=True):
        # 记下调用方是否要求静默，供 test_reconciler_calls_lpm_silently 之外的
        # 用例断言（签名必须与真 LPM 一致，否则桩会掩盖真实的 TypeError）
        self.log_mismatch_seen.append(log_mismatch)
        local, exchange = self.table[symbol]
        return {
            "matched": abs(local - exchange) < 1e-8,
            "local": local,
            "exchange": exchange,
            "diff": exchange - local,
        }


@pytest.fixture
def fake_lpm(monkeypatch):
    """替换 LPM 单例，让 run_reconcile_once 用预设对账结果。"""
    holder = {}

    def _install(table):
        import backend.services.live_position_manager as _lpm_mod
        holder["lpm"] = _FakeLPM(table)
        monkeypatch.setattr(
            _lpm_mod, "live_position_manager", holder["lpm"], raising=False,
        )

    return _install


# ── 1. 判据本身 ───────────────────────────────────────────────────

def test_external_when_local_flat_but_exchange_holds(state_file):
    """本地账本为 0 而交易所有仓 → 外部手工持仓。"""
    assert lpr._is_external_position(
        "VIRTUAL", {"local": 0.0, "exchange": 2222.0, "diff": 2222.0}) is True


def test_not_external_when_local_has_ledger(state_file):
    """本地有账本但数量不符 → 真实漂移，必须继续走 MISMATCH。"""
    assert lpr._is_external_position(
        "BTC", {"local": 0.5, "exchange": 0.8, "diff": 0.3}) is False


def test_not_external_when_exchange_flat(state_file):
    """本地有仓、交易所已平 → 是自动对齐的 close_all 场景，不能当外部仓。"""
    assert lpr._is_external_position(
        "BTC", {"local": 0.5, "exchange": 0.0, "diff": -0.5}) is False


def test_env_declared_symbol_is_external(state_file, monkeypatch):
    """显式声明的符号强制视为外部仓（账本残留导致自动判据失效时的人工兜底）。"""
    monkeypatch.setenv("LIVE_RECONCILE_EXTERNAL_SYMBOLS", "virtual, xpl")
    # 即使本地有账本，也按声明摘除
    assert lpr._is_external_position(
        "VIRTUAL", {"local": 100.0, "exchange": 2222.0, "diff": 2122.0}) is True
    assert lpr._is_external_position(
        "XPL", {"local": 0.0, "exchange": 6666.0, "diff": 6666.0}) is True
    assert lpr._is_external_position(
        "BTC", {"local": 0.5, "exchange": 0.8, "diff": 0.3}) is False


def test_malformed_rec_does_not_raise(state_file):
    assert lpr._is_external_position("BTC", {}) is False
    assert lpr._is_external_position("BTC", {"local": "x", "exchange": None}) is False


# ── 2. 对账主流程 ─────────────────────────────────────────────────

def test_external_excluded_from_mismatch(state_file, fake_lpm, caplog):
    """手工仓不再产生 MISMATCH ERROR，改为 INFO 并登记到状态文件。"""
    fake_lpm({"VIRTUAL": (0.0, 2222.0)})

    with caplog.at_level("INFO"):
        out = lpr.run_reconcile_once(
            None, 188,
            lambda aid: [{"symbol": "VIRTUAL", "size": 2222.0, "side": "long"}],
        )

    assert out["ok"] is True
    r = out["results"][0]
    assert r["external"] is True
    assert r["matched"] is True, "外部仓不应被判为不一致"

    assert not [x for x in caplog.records if x.levelname in ("ERROR", "CRITICAL")], \
        "外部手工持仓不得再产生 ERROR/CRITICAL 告警"
    assert any("外部手工持仓" in x.message for x in caplog.records)

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert "ext:a188:VIRTUAL" in saved
    ext = saved["ext:a188:VIRTUAL"]
    assert ext["symbol"] == "VIRTUAL"
    assert ext["qty"] == 2222.0
    assert ext["source"] == "auto"
    assert ext["first_seen"]
    assert "a188:VIRTUAL" not in saved, "不应再残留 mismatch 计数"


def test_external_logs_only_on_qty_change(state_file, fake_lpm, caplog):
    """数量不变时静默，变化时才打印 —— 否则每 10 分钟一条照样是刷屏。"""
    fake_lpm({"XPL": (0.0, 6666.0)})
    fetch = lambda aid: [{"symbol": "XPL", "size": 6666.0, "side": "long"}]

    with caplog.at_level("INFO"):
        lpr.run_reconcile_once(None, 188, fetch)          # 首次：应打印
    first = len([x for x in caplog.records if "外部手工持仓" in x.message])
    caplog.clear()

    with caplog.at_level("INFO"):
        lpr.run_reconcile_once(None, 188, fetch)          # 数量未变：应静默
        lpr.run_reconcile_once(None, 188, fetch)
    silent = len([x for x in caplog.records if "外部手工持仓" in x.message])

    fake_lpm({"XPL": (0.0, 8000.0)})
    caplog.clear()
    with caplog.at_level("INFO"):
        lpr.run_reconcile_once(
            None, 188,
            lambda aid: [{"symbol": "XPL", "size": 8000.0, "side": "long"}],
        )                                                  # 数量变化：应打印
    changed = len([x for x in caplog.records if "外部手工持仓" in x.message])

    assert first == 1
    assert silent == 0, "数量未变时不应重复打印"
    assert changed == 1


def test_real_drift_still_alerts(state_file, fake_lpm, caplog):
    """本地有账本的真实漂移必须照常 ERROR —— 降噪不能变成掩盖。"""
    fake_lpm({"BTC": (0.5, 0.8)})

    with caplog.at_level("INFO"):
        out = lpr.run_reconcile_once(
            None, 188,
            lambda aid: [{"symbol": "BTC", "size": 0.8, "side": "long"}],
        )

    assert out["results"][0]["matched"] is False
    assert any(
        x.levelname == "ERROR" and "MISMATCH" in x.message
        for x in caplog.records
    ), "真实账本漂移必须保留 ERROR 告警"


def test_external_survives_day_rollover(state_file, fake_lpm):
    """跨日只重置 mismatch 计数，外部仓登记与 first_seen 要留住。"""
    fake_lpm({"XPL": (0.0, 6666.0)})
    fetch = lambda aid: [{"symbol": "XPL", "size": 6666.0, "side": "long"}]
    lpr.run_reconcile_once(None, 188, fetch)

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    _first_seen = saved["ext:a188:XPL"]["first_seen"]
    saved["day"] = "1999-01-01"       # 伪造跨日
    state_file.write_text(json.dumps(saved), encoding="utf-8")

    lpr.run_reconcile_once(None, 188, fetch)

    after = json.loads(state_file.read_text(encoding="utf-8"))
    assert "ext:a188:XPL" in after, "跨日不应丢掉外部仓登记"
    assert after["ext:a188:XPL"]["first_seen"] == _first_seen


def test_external_cleared_when_manually_closed(state_file, fake_lpm):
    """手工平仓后（交易所无此符号）登记应被清理，不在面板长期残留。"""
    fake_lpm({"XPL": (0.0, 6666.0)})
    lpr.run_reconcile_once(
        None, 188, lambda aid: [{"symbol": "XPL", "size": 6666.0, "side": "long"}])
    assert "ext:a188:XPL" in json.loads(state_file.read_text(encoding="utf-8"))

    fake_lpm({})
    lpr.run_reconcile_once(None, 188, lambda aid: [])

    assert "ext:a188:XPL" not in json.loads(
        state_file.read_text(encoding="utf-8"))


# ── 3. 健康面板读取 ───────────────────────────────────────────────

def test_get_external_positions_shape(state_file, fake_lpm):
    """面板读取接口返回结构化列表（前端不必自己解析 key 前缀）。"""
    fake_lpm({"VIRTUAL": (0.0, 2222.0), "XPL": (0.0, 6666.0)})
    lpr.run_reconcile_once(None, 188, lambda aid: [
        {"symbol": "VIRTUAL", "size": 2222.0, "side": "long"},
        {"symbol": "XPL", "size": 6666.0, "side": "long"},
    ])

    rows = lpr.get_external_positions()

    assert [r["symbol"] for r in rows] == ["VIRTUAL", "XPL"]
    assert rows[0]["account_id"] == 188
    assert rows[0]["exchange_qty"] == 2222.0
    assert set(rows[0]) >= {
        "account_id", "symbol", "exchange_qty", "first_seen", "last_seen", "source",
    }


def test_health_endpoint_exposes_external_positions():
    """/live-guard-status 必须单列外部持仓字段（面板展示接线）。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "api", "unified_account_routes.py")
    with open(path, encoding="utf-8") as fh:
        src = "".join(ln for ln in fh if not ln.strip().startswith("#"))
    assert '"external_positions"' in src
    assert "get_external_positions" in src


# ── 4. LPM 底层日志分级（2026-09-02 重启后实测补齐）────────────────

def _lpm_reconcile(monkeypatch, *, local, exchange, **kw):
    """直接调 LivePositionManager.reconcile，net_size 用桩注入。"""
    from types import SimpleNamespace

    from backend.services.live_position_manager import LivePositionManager

    mgr = LivePositionManager()
    monkeypatch.setattr(
        mgr, "get_net_position",
        lambda db, aid, sym: SimpleNamespace(net_size=local),
    )
    return mgr.reconcile(None, 1, "VIRTUAL/USDT:USDT", exchange, 1.0, **kw)


def test_lpm_mismatch_logs_warning_by_default(monkeypatch, caplog):
    """默认仍记 WARNING —— 健康检查/手动对账等调用方行为不变。"""
    with caplog.at_level(logging.WARNING):
        rec = _lpm_reconcile(monkeypatch, local=0.0, exchange=2222.0)

    assert rec["matched"] is False
    assert any("reconcile MISMATCH" in r.message for r in caplog.records)


def test_lpm_mismatch_silent_when_disabled(monkeypatch, caplog):
    """log_mismatch=False 时不记日志，但判定结果不变。

    周期性对账循环走这条路径：手工持仓会让 LPM 每 120s × 每账户 × 每币刷一条
    WARNING（实测 VIRTUAL/XPL × 5 个账户 = 每分钟 5 条），而它分不清「用户手工
    开的仓」和「账本真漂移」。判定交给 reconciler 按情况记 INFO 或 ERROR。
    """
    with caplog.at_level(logging.DEBUG):
        rec = _lpm_reconcile(monkeypatch, local=0.0, exchange=2222.0,
                             log_mismatch=False)

    assert rec["matched"] is False, "静默只影响日志，不得影响对账判定"
    assert rec["local"] == 0.0 and rec["exchange"] == 2222.0
    assert not any("reconcile MISMATCH" in r.message for r in caplog.records)


def test_reconciler_calls_lpm_silently():
    """契约：周期对账必须以 log_mismatch=False 调用 LPM。"""
    import inspect

    src = inspect.getsource(lpr.run_reconcile_once)
    live = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "log_mismatch=False" in live, (
        "周期对账未静默 LPM 日志 → 手工持仓噪音会盖过真实漂移告警"
    )
