# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化] 采集宇宙必须 ⊇ 分析宇宙。

## 根因
`_depth_symbols()` 原先只取 `get_trade_universe_symbols()`（实测 ≈13–18 个），
而主脑还会分析**看板里的 AI 候选**（实测 SUI/PLAY/MU/USELESS/DOGE/1000PEPE…）。
看板币的长周期（4h/1d）永不回填 ⇒ 陈旧 ⇒ 主脑判 `K线:4h/1d` **硬缺项**
⇒ `consensus_is_tradeable` 短路 False ⇒ 论题被自动拒（报告 §12）。

本文件锁住：
1. 看板标的会被读出（mid/long/midlong 视野、listed、未过期）；
2. 它们会被**并入**回填宇宙（且不挤掉核心币前置）；
3. 回滚开关与"失败不阻塞"都能生效。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import kline_history_sync as H  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KLINE_DEPTH_BACKFILL_INCLUDE_BOARD", raising=False)
    monkeypatch.delenv("KLINE_DEPTH_BACKFILL_SYMBOLS", raising=False)
    yield


def test_board_symbols_are_merged_into_universe(monkeypatch):
    # ⚠️ `_depth_symbols` 里是**局部 import**（`from ...kline_realtime_collector import
    # get_trade_universe_symbols`）⇒ 必须打在**源模块**上，打在 H 上无效（我第一版就踩了）。
    import backend.services.kline_realtime_collector as KRC
    monkeypatch.setattr(KRC, "get_trade_universe_symbols", lambda: ["BTC", "XRP"], raising=False)
    monkeypatch.setattr(H, "_analysis_board_symbols", lambda limit=60: ["SUI", "ADA", "WIF"])
    out = H._depth_symbols(max_symbols=60)
    assert "SUI" in out and "ADA" in out and "WIF" in out, f"看板标的未并入: {out}"
    assert out[:3] == ["BTC", "ETH", "SOL"], "核心币必须仍被前置"


def test_rollback_flag_skips_board(monkeypatch):
    """回滚开关在**真函数**里生效（第一版我把整个函数打补丁 ⇒ 绕过了开关本身，测了个寂寞）。"""
    import backend.services.kline_realtime_collector as KRC
    monkeypatch.setattr(KRC, "get_trade_universe_symbols", lambda: ["BTC"], raising=False)
    monkeypatch.setenv("KLINE_DEPTH_BACKFILL_INCLUDE_BOARD", "0")
    assert H._analysis_board_symbols() == [], "开关置 0 时不得读看板"
    out = H._depth_symbols(max_symbols=60)
    assert out == ["BTC", "ETH", "SOL"], f"只应有既定宇宙（+核心前置）: {out}"


def test_board_failure_does_not_block(monkeypatch):
    import backend.services.kline_realtime_collector as KRC
    monkeypatch.setattr(KRC, "get_trade_universe_symbols", lambda: ["BTC", "XRP"], raising=False)

    def _raise(limit=60):
        raise RuntimeError("db down")

    monkeypatch.setattr(H, "_analysis_board_symbols", _raise)
    # 注意：_depth_symbols 内部已 try/except 包住看板并入 ⇒ 不应抛
    out = H._depth_symbols(max_symbols=60)
    assert "BTC" in out and "XRP" in out, "看板读取失败不得影响既定宇宙"


def test_board_query_is_utc_naive_and_horizon_scoped():
    """口径必须写死：**UTC-naive** 比较 + 只看 mid/long/midlong。

    `valid_until` 由 `_persist_board` 写成 `datetime.now(timezone.utc).replace(tzinfo=None)`；
    若用 `now()`（timestamptz）比较，会按会话时区偏移 8 小时 ⇒ 过期看板被当成有效。

    ⚠️ 只检查 **SQL 字面量**，不检查整段源码 —— 否则会撞上我自己写的解释性注释
    （"不得用 now()"），这正是本仓库 F361 记录过的"注释被当成缺陷"假阳性。
    """
    src = (ROOT / "backend" / "services" / "kline_history_sync.py").read_text(encoding="utf-8")
    i = src.index("def _analysis_board_symbols")
    seg = src[i:i + 2600]
    sql = seg[seg.index("SELECT DISTINCT symbol"):seg.index("ORDER BY symbol LIMIT")]
    assert "now()" not in sql, "SQL 里不得用 now()（会话时区会造成 8 小时偏移）"
    assert ":now_utc" in sql, "必须用显式 UTC-naive 绑定参数"
    assert "replace(tzinfo=None)" in seg, "边界必须是 UTC-naive"
    for h in ("mid", "long", "midlong"):
        assert h in sql
    assert "listed IS TRUE" in sql
