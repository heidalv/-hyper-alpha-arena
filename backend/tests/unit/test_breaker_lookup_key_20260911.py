# -*- coding: utf-8 -*-
"""[§81 契约 2026-09-11 / 决策 P22-A] 通道熔断的**查询键必须与写入键同源**。

缺陷 #66（§81.3）：`should_block()` 里查熔断用的键取自 `req.reason`，而归因落库用的
键取自 `req.exit_channel`（`_execute_raw` 里 `_reason = req.exit_channel or req.reason`）。
`master_execution.py` 两处构造请求时 `reason="master_{mode}"`、`exit_channel="master_{mode}_{action}"`
**不同名** ⇒ 闸门查 `mid|master_running`、归因只写 `mid|master_running_close` ⇒ Master 半边
永不命中（"名义接线、实际惰性"）。

本文件锁住：
  ① 行为：Master 风格请求（reason/exit_channel 不同名）下，闸门查询键 = 落库键；
  ② 反漂移：两侧取键优先级必须是同一顺序（源码级护栏，防止再被改回）；
  ③ MLTO 路径不受影响（传入 == 落库的同一字符串）；
  ④ 保护性通道 / 硬退出仍不参与熔断。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services import unified_exit_executor as ue
from backend.services.exit import channel_breaker_gate as gate


def _master_req(action: str = "close", mode: str = "running", tier: str = "mid"):
    """复刻 master_execution.py 的构造方式（reason 与 exit_channel 不同名）。"""
    return ue.ExitExecuteRequest(
        db=None,
        account_id=14,
        symbol="BTCUSDT",
        action=action,
        pos={"symbol": "BTCUSDT", "side": "long", "timeframe_tier": tier, "entry_price": 100.0},
        exit_channel=f"master_{mode}_{action}",
        reason=f"master_{mode}",
        mode=mode,
    )


def _captured_key(monkeypatch, req) -> str:
    seen = {}

    def _fake(reason, tier):
        seen["reason"] = reason
        seen["tier"] = tier
        return False, "captured"

    monkeypatch.setattr(gate, "should_suppress", _fake)
    ue.unified_exit_executor.should_block(req)
    return seen.get("reason", "")


def test_master_style_request_queries_the_recorded_key(monkeypatch):
    """核心回归：查询键必须是**会落库的那个键**（exit_channel 优先）。"""
    key = _captured_key(monkeypatch, _master_req(action="close", mode="running"))
    assert key == "master_running_close", (
        f"闸门查的是 {key!r}，而归因会写 'master_running_close' ⇒ Master 半边永不命中（缺陷 #66 复发）"
    )


def test_hold_timeout_review_key_is_reachable(monkeypatch):
    """`hold_timeout_review` 通道同样要能被查到（原实现查的是 `master_<mode>`）。"""
    req = ue.ExitExecuteRequest(
        db=None, account_id=14, symbol="ETHUSDT", action="close",
        pos={"symbol": "ETHUSDT", "side": "long", "timeframe_tier": "mid", "entry_price": 1.0},
        exit_channel="hold_timeout_review", reason="master_running",
    )
    assert _captured_key(monkeypatch, req) == "hold_timeout_review"


def test_reduce_action_key_matches_writer(monkeypatch):
    """`reduce` 的落库键同样是 `exit_channel or reason`（`_execute_raw` 第二分支）。"""
    key = _captured_key(monkeypatch, _master_req(action="reduce", mode="defensive"))
    assert key == "master_defensive_reduce"


def test_precedence_matches_writer_in_source():
    """反漂移：查询侧与写入侧的取键顺序必须一致（源码级断言）。"""
    block_src = inspect.getsource(ue.UnifiedExitExecutor.should_block)
    raw_src = inspect.getsource(ue.UnifiedExitExecutor._execute_raw)
    assert 'req.exit_channel or ""' in block_src and 'str(req.reason or "")' in block_src, \
        "查询侧未按 exit_channel 优先取键"
    q = block_src.index('_rsn = str(req.exit_channel or "")')
    assert q > 0
    assert "or str(req.reason or \"\")" in block_src[q:q + 80], "查询侧优先级写反了"
    assert "_reason = req.exit_channel or req.reason" in raw_src, \
        "写入侧口径变了 ⇒ 本护栏需要同步更新（两处必须一起改）"


def test_mlto_style_single_string_unaffected():
    """MLTO 路径：`_exec_close` 把同一个字符串既交给闸门又落库 ⇒ 行为不变。"""
    assert gate.channel_of("trend_broken: 方向破坏") == "trend_broken"
    assert gate.channel_of("midlong: review") == "midlong"


def test_protected_and_hard_exit_still_bypass(monkeypatch):
    """保护性通道与硬退出仍不参与熔断（P19-B 的安全边界不得被本次修复削弱）。"""
    assert gate.is_protected(gate.channel_of("sl")) is True
    assert gate.is_protected(gate.channel_of("max_hold_timeout")) is True
    assert gate.is_protected(gate.channel_of("profit_drawdown_full")) is True
    # 硬退出（liquidation/emergency…）在 should_block 里根本不进熔断分支
    req = ue.ExitExecuteRequest(
        db=None, account_id=14, symbol="BTCUSDT", action="close",
        pos={"symbol": "BTCUSDT", "side": "long", "timeframe_tier": "mid"},
        exit_channel="liquidation", reason="liquidation",
    )
    called = {"n": 0}

    def _fake(reason, tier):
        called["n"] += 1
        return False, "x"

    monkeypatch.setattr(gate, "should_suppress", _fake)
    ue.unified_exit_executor.should_block(req)
    assert called["n"] == 0, "硬退出不应进入通道熔断判定"


# ── ③ 抑制事件的**可追溯性**（§85 修复：主平仓路径此前不写事件流）───────────
def test_master_close_path_appends_suppression_event():
    """主平仓路径必须把 `blocked` 事件写进会话事件流（与部分平仓路径/MLTO 路径一致）。

    此前该分支只有 `continue` ⇒ 通道熔断的抑制在**事件流里查不到**（只剩日志一行），
    而"抑制离场"是风险决策，必须可追溯（目标③）。
    """
    import backend.services.full_auto.master_execution as me

    src = inspect.getsource(me)
    i_blocked = src.index("if _gate.blocked:")
    window = src[i_blocked:i_blocked + 900]
    assert "append_event" in window and "_gate.event_type" in window, \
        "主平仓路径被抑制时没有写事件流（抑制不可追溯）"
    assert "continue" in window
