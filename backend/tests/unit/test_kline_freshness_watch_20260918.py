# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化·③] 「K 线陈旧清单」常态可见的契约。

## 为什么
事故教训：SUI 的 1d 陈旧 **102 小时**无人发现，直到主脑因它判 `K线:*` 硬缺项、
论题被自动拒（报告 §12）。此前**只能靠临时脚本查** ⇒ 等于没人看。

本文件锁住：
1. 陈旧判定口径（3 个周期长度；无数据也算陈旧；未知周期不判）；
2. 格式化输出必须**带标的/周期/年龄**且给出总数；
3. 有陈旧 ⇒ `warning`，全新鲜 ⇒ `info`（级别不同，便于告警规则区分）；
4. 失败/开关关 ⇒ 不抛、不影响采集；
5. DC 进程真的把它挂起来了（源码级接线）。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import kline_freshness_watch as W  # noqa: E402


def test_classify_splits_stale_and_fresh():
    now = 1_700_000_000.0
    rows = [
        ("BTC", "1h", now - 3600),            # 1h 前 ⇒ 新鲜（阈值 3h）
        ("SUI", "1d", now - 102 * 3600),      # 102h ⇒ 陈旧（阈值 72h）
        ("ADA", "4h", now - 20 * 3600),       # 20h ⇒ 陈旧（阈值 12h）
        ("UNI", "15m", now - 600),            # 10 分钟 ⇒ 新鲜（阈值 0.75h=45min）
        ("X", "1w", now),                     # 未知周期 ⇒ 不判
    ]
    stale, fresh = W.classify(rows, now=now)
    assert [(s["symbol"], s["period"]) for s in stale] == [("SUI", "1d"), ("ADA", "4h")]
    assert {f["symbol"] for f in fresh} == {"BTC", "UNI"}
    assert all("1w" not in (s["period"], f["period"]) for s in stale for f in fresh)


def test_missing_data_counts_as_stale():
    now = 1_700_000_000.0
    stale, _ = W.classify([("PLAY", "4h", None)], now=now)
    assert stale and stale[0]["reason"] == "无数据" and stale[0]["age_h"] is None


def test_universe_missing_rows_are_filled(tmp_path, monkeypatch):
    """GROUP BY 不会返回"一行都没有"的组合 ⇒ 必须补成"无数据"，否则漏报。"""
    monkeypatch.setattr(W, "stale_snapshot", W.stale_snapshot)  # 保持真实实现
    # 构造：只有 BTC/1h 有数据，其余全缺
    import backend.database.connection as C

    class _FakeRow(tuple):
        pass

    class _FakeRes:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _FakeDB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a, **_k):
            return _FakeRes([("BTC", "1h", time.time() - 60)])

    monkeypatch.setattr(C, "MarketSessionLocal", lambda: _FakeDB(), raising=False)
    stale, checked = W.stale_snapshot(["BTC", "SUI"])
    assert checked == 2 * len(W.ANALYSIS_TFS), f"应检查 标的×周期 全组合，实际 {checked}"
    syms = {s["symbol"] for s in stale}
    assert "SUI" in syms, "完全无数据的标的必须被报出来"


def test_format_line_contains_symbol_period_age_and_total():
    stale = [{"symbol": "SUI", "period": "1d", "age_h": 102.0, "limit_h": 72.0},
             {"symbol": "ADA", "period": "4h", "age_h": None, "limit_h": 12.0}]
    line = W.format_line(stale, 40)
    assert "SUI/1d(102.0h)" in line and "ADA/4h(无数据)" in line
    assert "2/40" in line
    assert "硬缺项" in line, "应说明后果（主脑判硬缺项 ⇒ 论题被自动拒）"


def test_log_level_warning_when_stale_info_when_fresh(monkeypatch, caplog):
    monkeypatch.setattr(W, "stale_snapshot", lambda symbols=None: (
        [{"symbol": "SUI", "period": "1d", "age_h": 102.0, "limit_h": 72.0}], 40))
    with caplog.at_level(logging.INFO):
        line = W.log_summary()
    assert line and caplog.records[-1].levelno == logging.WARNING

    caplog.clear()
    monkeypatch.setattr(W, "stale_snapshot", lambda symbols=None: ([], 40))
    with caplog.at_level(logging.INFO):
        line = W.log_summary()
    assert line and caplog.records[-1].levelno == logging.INFO


def test_disabled_switch_and_db_failure_are_silent(monkeypatch):
    monkeypatch.setenv("KLINE_FRESHNESS_WATCH_ENABLED", "0")
    assert W.watch_once_if_enabled() is None
    monkeypatch.delenv("KLINE_FRESHNESS_WATCH_ENABLED", raising=False)
    # DB 失败 ⇒ 返回空、不抛
    import backend.database.connection as C

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(C, "MarketSessionLocal", _boom, raising=False)
    assert W.stale_snapshot(["BTC"]) == ([], 0)


def test_dc_process_wires_the_watch():
    src = (ROOT / "backend" / "workers" / "market_data_center.py").read_text(encoding="utf-8")
    assert "start_watch_thread" in src, "数据中心未挂载陈旧清单扫描"
    assert "freshness_watch" in src, "组件状态里应可见（运维能确认它起来了）"
