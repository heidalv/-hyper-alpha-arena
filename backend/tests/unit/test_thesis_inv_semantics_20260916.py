# -*- coding: utf-8 -*-
"""[调研轮18 2026-09-16] 失效价触发**与条件原文语义对齐**的契约测试。

## 被锁定的不一致

6 笔 `thesis_invalidation` 平仓的失效条件原文都写「日线/4h **收盘**跌破 X」，
而实现是「mark 价一碰就平」⇒ 逐笔回放 **4/6 的 1h 收盘仍在失效价之内**
（原文条件从未成立就被砍），这 4 笔出场后 24h 分别走高 +10.9%/+3.8%/+5.5%/+3.8%；
另 2 笔（SOL/VIRTUAL）收盘确实收破、出场正确（VIRTUAL 之后继续跌 −9.1%）。

## 锁定语义（对齐后）

1. 剧烈破位（≥ ESCAPE_DEPTH_PCT，默认 2%）⇒ 立即触发（对应原文"无法收回"）；
2. CONFIRM_TF（默认 1h）**已收盘** K 线收在失效价之外 ⇒ 触发（对应"收盘跌破"）；
3. 否则**本轮不触发**（少砍早单；硬止损/追踪/分段止盈照旧兜底）；
4. 开关关闭 / 无 mark / K 线不可用 / 异常 ⇒ 保持旧行为（触发）；
5. 接线护栏：`resolve_thesis_hard_exit` 必须真的调用它并记录原因。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_position_manager as mpm  # noqa: E402


def _pos(mark=100.0, sym="UNI", side="long"):
    return {"symbol": sym, "side": side, "mark_price": mark, "current_price": mark}


def _bars(closes):
    return [{"timestamp": 1_700_000_000 + i * 3600, "open": c, "high": c, "low": c,
             "close": c, "volume": 1.0} for i, c in enumerate(closes)]


def _patch_bars(monkeypatch, closes):
    import backend.services.market_data as md

    monkeypatch.setattr(md, "get_kline_data", lambda *a, **k: _bars(closes), raising=True)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    from backend.config import settings as s

    monkeypatch.setattr(s, "MIDLONG_THESIS_INV_REQUIRE_CLOSE", True, raising=False)
    monkeypatch.setattr(s, "MIDLONG_THESIS_INV_ESCAPE_DEPTH_PCT", 0.02, raising=False)
    monkeypatch.setattr(s, "MIDLONG_THESIS_INV_CONFIRM_TF", "1h", raising=False)
    yield


def test_escape_depth_triggers_immediately(monkeypatch):
    """剧烈破位（≥2%）不等收盘 —— 真破位照旧立即出场。"""
    _patch_bars(monkeypatch, [100.0, 100.5, 100.8])   # 收盘还在内侧
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=97.5), ipx=100.0, side="long")
    assert ok is True and "剧烈破位" in why, why


def test_close_beyond_triggers(monkeypatch):
    """1h 收盘确实收在失效价之外 ⇒ 触发（原文"收盘跌破"成立）。"""
    _patch_bars(monkeypatch, [100.5, 99.5, 99.8])      # rows[-2]=99.5 < 100
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=99.9), ipx=100.0, side="long")
    assert ok is True and "已破" in why, why


def test_shallow_touch_with_close_inside_does_not_trigger(monkeypatch):
    """浅穿透但收盘未破 ⇒ 原文条件未成立 ⇒ 本轮不触发（= 少砍的那 4/6）。"""
    _patch_bars(monkeypatch, [100.0, 100.05, 99.95])
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=99.95), ipx=100.0, side="long")
    assert ok is False and "条件未成立" in why, why


def test_short_symmetric(monkeypatch):
    _patch_bars(monkeypatch, [100.0, 100.4, 100.6])
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=100.5, side="short"), ipx=100.0, side="short")
    assert ok is True, why
    _patch_bars(monkeypatch, [100.0, 99.9, 100.05])
    ok2, why2 = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=100.1, side="short"), ipx=100.0, side="short")
    assert ok2 is False, why2


def test_switch_off_keeps_old_touch_behavior(monkeypatch):
    from backend.config import settings as s

    monkeypatch.setattr(s, "MIDLONG_THESIS_INV_REQUIRE_CLOSE", False, raising=False)
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=99.99), ipx=100.0, side="long")
    assert ok is True and "触碰即平" in why, why


def test_fail_open_on_kline_error(monkeypatch):
    import backend.services.market_data as md

    def _boom(*a, **k):
        raise RuntimeError("dc down")

    monkeypatch.setattr(md, "get_kline_data", _boom, raising=True)
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position=_pos(mark=99.95), ipx=100.0, side="long")
    assert ok is True and "kline_unavailable" in why, why


def test_no_mark_keeps_old_behavior():
    ok, why = mpm.thesis_invalidation_semantics_ok(
        position={"symbol": "UNI", "side": "long"}, ipx=100.0, side="long")
    assert ok is True and why == "no_mark"


def test_wired_into_resolve_thesis_hard_exit():
    src = inspect.getsource(mpm.resolve_thesis_hard_exit)
    assert "thesis_invalidation_semantics_ok(" in src, "语义对齐未接线"
    assert 'return ("thesis_invalidation", th)' in src


def test_deployed_env_values():
    import os

    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
    assert os.environ.get("MIDLONG_THESIS_INV_REQUIRE_CLOSE", "").lower() == "true"
    assert float(os.environ.get("MIDLONG_THESIS_INV_ESCAPE_DEPTH_PCT", "0")) == pytest.approx(0.02)
    assert os.environ.get("MIDLONG_THESIS_INV_CONFIRM_TF", "") == "1h"
