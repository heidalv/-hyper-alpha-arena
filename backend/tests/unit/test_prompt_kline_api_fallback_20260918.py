# -*- coding: utf-8 -*-
"""[2026-09-18 根因修复·K线兜底] prompt 侧 K 线的 DB→API 兜底契约。

## 为什么（本轮查到的"开不出仓"直接原因链）
DB 聚合对陈旧数据 **fail-closed**（`[KlineAgg] …基准所 K 线过期…拒绝聚合返回`）
⇒ `context_pack._klines` 空 ⇒ `brain._kline_ok` 报 `K线:4h/1d`
⇒ `missing_blocks_open` 视为**硬缺项** ⇒ `hard_miss=True`
⇒ `consensus_is_tradeable` **直接短路 False** ⇒ 论题被拒（30 分钟退避）
⇒ **这些标的永远没有可执行论题**。

实测（14:1x `refresh` 日志）：MU `K线:4h(数据不足)`×6、SUI `K线:4h/1d`、
PLAY `K线:4h/1d(完整)` —— 与待办 B29 的陈旧清单（SUI/PLAY/COTI/ONE/ICP/AVAX/ADA）吻合。

而协调器那条路早有 API 兜底，prompt 这条路没有 ⇒ 本修复补齐，并保持窗口口径一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import agent_deep_context as A  # noqa: E402


def _rows(n: int) -> list:
    return [{"timestamp": 1700000000 + i * 3600, "open": 1.0, "high": 2.0,
             "low": 0.5, "close": 1.5, "volume": 10.0} for i in range(n)]


@pytest.fixture(autouse=True)
def _no_snapshot(monkeypatch):
    # 关掉快照复用分支，直测 DB→API 兜底
    monkeypatch.setenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", "false")
    monkeypatch.delenv("PROMPT_KLINE_API_FALLBACK", raising=False)
    yield


def test_api_fallback_used_when_db_insufficient(monkeypatch):
    import backend.services.kline_data_service as K
    monkeypatch.setattr(K.kline_service, "get_aggregated_klines",
                        lambda *a, **k: [], raising=False)
    monkeypatch.setattr(A, "_api_klines_fallback",
                        lambda s, t, c: _rows(c))
    rows = A._fetch_klines_for_prompt("SUI", "4h", 20)
    assert len(rows) == 20, "DB 空时必须用 API 兜底补齐"
    assert rows[-1]["timestamp"] == 1700000000 + 19 * 3600, "应取尾部 count 根（窗口与 DB 路径一致）"


def test_db_sufficient_does_not_call_api(monkeypatch):
    import backend.services.kline_data_service as K
    monkeypatch.setattr(K.kline_service, "get_aggregated_klines",
                        lambda *a, **k: _rows(20), raising=False)
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        return _rows(20)

    monkeypatch.setattr(A, "_api_klines_fallback", _boom)
    rows = A._fetch_klines_for_prompt("BTC", "4h", 20)
    assert len(rows) == 20
    assert called["n"] == 0, "DB 已足够时不得触发 API（避免无谓网络/429 风险）"


def test_partial_db_is_topped_up(monkeypatch):
    """DB 只给几根（陈旧时也可能）⇒ 也要兜底：否则主脑判硬缺项。"""
    import backend.services.kline_data_service as K
    monkeypatch.setattr(K.kline_service, "get_aggregated_klines",
                        lambda *a, **k: _rows(3), raising=False)
    monkeypatch.setattr(A, "_api_klines_fallback", lambda s, t, c: _rows(c))
    rows = A._fetch_klines_for_prompt("PLAY", "4h", 20)
    assert len(rows) == 20


def test_rollback_flag_disables_fallback(monkeypatch):
    import backend.services.kline_data_service as K
    monkeypatch.setattr(K.kline_service, "get_aggregated_klines",
                        lambda *a, **k: [], raising=False)
    monkeypatch.setenv("PROMPT_KLINE_API_FALLBACK", "0")
    rows = A._fetch_klines_for_prompt("SUI", "4h", 20)
    assert rows == [], "回滚开关关闭后必须逐字节回到旧行为"


def test_api_failure_is_fail_open_to_db(monkeypatch):
    """API 失败 ⇒ 返回 DB 原值（不抛、不改变既有行为）。"""
    import backend.services.kline_data_service as K
    monkeypatch.setattr(K.kline_service, "get_aggregated_klines",
                        lambda *a, **k: _rows(5), raising=False)
    monkeypatch.setattr(A, "_api_klines_fallback", lambda s, t, c: [])
    rows = A._fetch_klines_for_prompt("ADA", "4h", 20)
    assert len(rows) == 5


def test_missing_blocks_open_treats_kline_as_hard():
    """把"为什么这个缺失是致命的"钉住：K线缺失属**硬**缺项 ⇒ 会短路接受判定。"""
    from backend.services.mlto.brain import missing_blocks_open
    assert missing_blocks_open(["K线:4h"]) is True
    assert missing_blocks_open(["现价"]) is True


def test_dc_only_short_circuits_without_calling_exchange(monkeypatch):
    """**本部署的实况**：DC_ONLY 下直连被设计禁止 ⇒ 必须立即返回空、不去撞墙。

    实测（2026-09-18 真实集成）：`[Coordinator] API获取K线失败 … DC_ONLY 模式不允许
    直接访问交易所` —— 若不短路，每个标的每个周期都会刷一条注定失败的告警。
    """
    import backend.services.market_data as MD
    monkeypatch.delenv("PROMPT_KLINE_API_FALLBACK", raising=False)
    monkeypatch.setattr(MD, "_dc_only_enabled", lambda: True, raising=False)
    called = {"n": 0}

    import backend.services.strategy_coordinator as SC
    monkeypatch.setattr(SC.StrategyCoordinator, "_fetch_klines_from_api",
                        staticmethod(lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _rows(50)),
                        raising=False)
    assert A._api_klines_fallback("AVAX", "4h", 20) == []
    assert called["n"] == 0, "DC_ONLY 下不得发起注定失败的交易所调用"


def test_non_dc_only_still_uses_api(monkeypatch):
    """非 DC_ONLY（放开直连）时兜底仍应工作 —— 保留给其它部署/将来放开。"""
    import backend.services.market_data as MD
    monkeypatch.delenv("PROMPT_KLINE_API_FALLBACK", raising=False)
    monkeypatch.setattr(MD, "_dc_only_enabled", lambda: False, raising=False)

    import backend.services.strategy_coordinator as SC
    monkeypatch.setattr(SC.StrategyCoordinator, "_fetch_klines_from_api",
                        staticmethod(lambda *a, **k: _rows(50)), raising=False)
    out = A._api_klines_fallback("AVAX", "4h", 20)
    assert len(out) == 20, "非 DC_ONLY 时应返回尾部 count 根"
