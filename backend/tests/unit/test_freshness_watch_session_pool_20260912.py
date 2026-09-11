# -*- coding: utf-8 -*-
"""[2026-09-12 F38y] 观察名单必须并入会话 AI 选币池（模式锁）。

现场：AVAX/INJ 是 AI 中线 sticky 候选（auto_coin_mid_symbols 池），观察名单
只并 env / 用户固定对 / ai_coin_unified 动态文件——候选被 sticky 后文件清空，
观察名单丢失该币 → AVAX/4h 陈旧 2.7 天 → trade 用途 fail-closed，
"AI 选得出币、中线下不了单"复现。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import kline_realtime_collector as krc  # noqa: E402


def test_watch_symbols_merges_session_auto_coin_pools():
    src = inspect.getsource(krc.KlineRealtimeCollector._freshness_watch_symbols)
    assert "auto_coin_symbols" in src, "观察名单必须并入会话短线选币池"
    assert "coin_select_candidates" in src, "观察名单必须并入 midlong 看板 approve 候选"
    assert "horizon = 'midlong'" in src
    assert "FullAutoSession" in src
    assert "running" in src and "defensive" in src and "paused" in src


def test_ai_coin_unified_merge_still_present():
    src = inspect.getsource(krc.KlineRealtimeCollector._freshness_watch_symbols)
    assert "ai_coin_unified" in src, "既有动态候选并入不得丢失"
