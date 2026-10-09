# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化] P0 采集的**请求级超时 + 有界重试**契约。

## 根因（实证）
`_collect_symbol_kline` 原先既无请求级超时也无重试 ⇒ 底层挂住时任务一直等，
直到**轮级** `KLINE_P0_TIMEOUT_S`(=90s) 到期被 `asyncio.wait_for` **整轮取消**。
`logs/data-center.log` 实测：**1383 轮 / 成功率 49.3% / 整轮全失败 656 轮(47.4%)**。
且取消走 `CancelledError`(BaseException)，原 `except Exception` 捕不到
⇒ 日志里 `Failed to collect kline` **0 条**，故障长期无痕。

本文件锁住三件事：
1. 挂死的请求被**请求级**超时切断（而不是拖到轮级）；
2. 有界重试真的发生，且次数受 `KLINE_P0_REQ_RETRY` 控制；
3. 回滚开关 `KLINE_P0_REQ_TIMEOUT_S=0` 能关掉请求级超时。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]   # unit→tests→backend→repo（写 parents[2] 会指到 backend/）
sys.path.insert(0, str(ROOT))

import backend.services.kline_realtime_collector as K  # noqa: E402


class _HangingCollector:
    """永远不返回的取数器（模拟代理抖动/连接停滞）。"""

    def __init__(self, hang: float = 60.0, result_on: int = 0):
        self.hang = hang
        self.calls = 0
        self.result_on = result_on  # 第 N 次调用返回数据（1-based）；0=永不

    async def fetch_current_kline(self, symbol, period):
        self.calls += 1
        if self.result_on and self.calls >= self.result_on:
            return None  # 返回 falsy：足以验证"重试被触发"，且不进入落库路径
        await asyncio.sleep(self.hang)
        return None


def _patch(monkeypatch, collector):
    monkeypatch.setattr(K.ExchangeDataSourceFactory, "get_collector",
                        staticmethod(lambda *a, **k: collector), raising=False)
    monkeypatch.setattr(K, "get_active_exchange", lambda: "binance", raising=False)


def test_hung_request_cut_by_per_request_timeout(monkeypatch, caplog):
    """挂死请求必须被**请求级**超时切断（而不是拖到轮级 90s）。"""
    monkeypatch.setenv("KLINE_P0_REQ_TIMEOUT_S", "0.3")
    monkeypatch.setenv("KLINE_P0_REQ_RETRY", "0")
    c = _HangingCollector(hang=60.0)
    _patch(monkeypatch, c)
    t0 = time.time()
    ok = asyncio.run(K.realtime_collector._collect_symbol_kline("SUI", "4h"))
    dt = time.time() - t0
    assert ok is False
    assert dt < 5.0, f"应在请求级超时内返回，实际 {dt:.1f}s（说明仍在拖轮级）"
    assert c.calls == 1


def test_retry_is_bounded_and_actually_happens(monkeypatch):
    monkeypatch.setenv("KLINE_P0_REQ_TIMEOUT_S", "0.2")
    monkeypatch.setenv("KLINE_P0_REQ_RETRY", "2")
    c = _HangingCollector(hang=60.0)
    _patch(monkeypatch, c)
    asyncio.run(K.realtime_collector._collect_symbol_kline("PLAY", "4h"))
    assert c.calls == 3, f"重试次数应为 1+2=3，实际 {c.calls}"


def test_retry_disabled_calls_once(monkeypatch):
    monkeypatch.setenv("KLINE_P0_REQ_TIMEOUT_S", "0.2")
    monkeypatch.setenv("KLINE_P0_REQ_RETRY", "0")
    c = _HangingCollector(hang=60.0)
    _patch(monkeypatch, c)
    asyncio.run(K.realtime_collector._collect_symbol_kline("ADA", "1h"))
    assert c.calls == 1


def test_rollback_flag_disables_request_timeout(monkeypatch):
    """=0 时不做请求级超时（回滚到旧行为）——用能返回的取数器验证不抛。"""
    monkeypatch.setenv("KLINE_P0_REQ_TIMEOUT_S", "0")
    monkeypatch.setenv("KLINE_P0_REQ_RETRY", "1")

    class _Quick:
        calls = 0

        async def fetch_current_kline(self, symbol, period):
            _Quick.calls += 1
            return None

    _patch(monkeypatch, _Quick())
    ok = asyncio.run(K.realtime_collector._collect_symbol_kline("BTC", "1m"))
    assert ok is False and _Quick.calls == 1


def test_round_level_timeout_still_present_as_backstop():
    """轮级超时仍是兜底（本改动只是让单任务不再拖垮整轮，不删兜底）。"""
    src = (ROOT / "backend" / "services" / "kline_realtime_collector.py").read_text(
        encoding="utf-8")
    assert "KLINE_P0_TIMEOUT_S" in src and "asyncio.wait_for" in src
    assert "KLINE_P0_REQ_TIMEOUT_S" in src, "新开关必须可配"
