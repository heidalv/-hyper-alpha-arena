# -*- coding: utf-8 -*-
"""[新目标 R4] ccxt 市场预载（冷启动风暴修复）的单测。

背景（§80 实测）：worker 重启后第 1 轮 P0 全灭（0ok/214err）、第 2 轮起 96.3%——
池内各线程首次 `_make_sync_ccxt` 各自 `load_markets`，冷启动风暴叠加当分钟任务拖爆整轮。
修复：start() 里把预载任务分发到池的**所有线程**（`_tls` 是线程本地缓存，单线程预载无效）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import backend.services.kline_collectors as KC
import backend.services.kline_realtime_collector as KR

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KLINE_CCXT_PREWARM", raising=False)
    yield


def test_switch_defaults_on_and_rollback(monkeypatch):
    assert KR._ccxt_prewarm_enabled() is True
    monkeypatch.setenv("KLINE_CCXT_PREWARM", "false")
    assert KR._ccxt_prewarm_enabled() is False
    monkeypatch.setenv("KLINE_CCXT_PREWARM", "garbage")
    assert KR._ccxt_prewarm_enabled() is False  # 非法值 fail-closed（与本仓其它开关同约定）


def test_prewarm_helper_exists_and_delegates():
    """prewarm_sync_ccxt 必须调用 _make_sync_ccxt（其内部才 load_markets）。"""
    src = (ROOT / "backend/services/kline_collectors.py").read_text(encoding="utf-8")
    i = src.find("def prewarm_sync_ccxt")
    assert i >= 0
    seg = src[i:i + 400]
    assert "_make_sync_ccxt(exchange_id)" in seg
    # 注释必须写明"线程本地 ⇒ 必须全线程预热"的教训
    assert "load_markets" in seg


def test_start_site_warms_all_pool_threads():
    """源码守卫：预载必须分发到池的所有线程（map over range(workers)），
    不能只在 start() 线程调一次——否则线程本地缓存暖不到池线程。"""
    src = (ROOT / "backend/services/kline_realtime_collector.py").read_text(encoding="utf-8")
    i = src.find("if _ccxt_prewarm_enabled():")
    assert i >= 0
    seg = src[i:i + 1400]
    assert "prewarm_sync_ccxt" in seg
    assert "_pool.map(lambda" in seg, "必须分发到池的所有线程（线程本地缓存）"
    assert "KLINE_CCXT_PREWARM" in src, "必须带开关说明"
    assert "get_active_exchange" in seg, "active 交易所必须从 exchange_config 取，而不是猜"
