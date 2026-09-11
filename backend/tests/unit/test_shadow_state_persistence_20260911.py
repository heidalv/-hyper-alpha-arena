# -*- coding: utf-8 -*-
"""[§81 契约 2026-09-11] 熔断标志的磁盘持久化（缺陷 #65）。

纪律来源：`data/fusion_attribution.json` 的 `breaker_shadow` 是**风控状态**，不是缓存：
它决定"出血通道是否还被允许执行离场"。任何"读一次就抹掉"或"空态就清零"的路径，
都会让 P20（重启重建）与回滚开关同时失效 —— 实测线上 11:30:14 发生 13 键/7 真 → 0 键。

本文件锁住三件事：
  ① **口径标记**：`shadow_mode="rolling"` 的文件必须原样保留标志；无标记的旧文件
     只迁移一次；
  ② **空态合并**：多进程防护分支必须按磁盘口径决定是否丢弃标志（旧实现无条件清空）；
  ③ **落盘一致性**：加载期重建出来的标志要落盘（仅在确有差异时写），
     且 pytest 进程不得写生产状态文件。
"""
from __future__ import annotations

import json

import pytest

import backend.services.source_attribution as sa

KEY = "mid|trend_broken"
RECENT = [0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0]


def _mk(marker: bool, flags: bool = True) -> dict:
    d = {
        "ts": 1.0,
        "tags": {"1": {"source": "mlto", "nature": "swing", "symbol": "BTC", "meta": {}, "ts": 1.0}},
        "stats": {"mlto|swing|BTC": {"n": 1, "wins": 0, "gross": -1.0, "fee": 0.0}},
        "shadow": {},
        "breaker": {KEY: {"n": 20, "wins": 4, "recent": list(RECENT)}},
        "breaker_shadow": ({KEY: True} if flags else {}),
    }
    if marker:
        d[sa.SHADOW_MODE_KEY] = sa.SHADOW_MODE_ROLLING
    return d


def _disk_flags(path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in (d.get("breaker_shadow") or {}).items() if v}


def _write(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── ① 口径标记 ──────────────────────────────────────────────────────────────

def test_rolling_marked_state_keeps_flags_on_disk(monkeypatch, tmp_path):
    """核心回归：滚动口径文件的磁盘标志**不得**被"读一次"抹掉。"""
    state = tmp_path / "state.json"
    _write(state, _mk(marker=True))
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "true")

    a = sa.SourceAttribution()
    assert a.exit_channel_shadow("trend_broken: x", "mid") is True
    assert _disk_flags(state) == {KEY: True}, "读一次状态文件就把熔断标志从磁盘抹掉了（缺陷 #65 复发）"


def test_legacy_state_migrates_once_then_flags_survive(monkeypatch, tmp_path):
    """旧文件（无标记）剥离一次并写标记；之后同文件再加载不得再剥离。"""
    state = tmp_path / "state.json"
    _write(state, _mk(marker=False))
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "false")

    a = sa.SourceAttribution()
    a._ensure_loaded()
    disk = json.loads(state.read_text(encoding="utf-8"))
    assert disk.get(sa.SHADOW_MODE_KEY) == sa.SHADOW_MODE_ROLLING, "迁移没有写入口径标记 ⇒ 会反复剥离"
    assert _disk_flags(state) == {}

    # 迁移后重新写入"现行"标志，再加载一次 ⇒ 必须保留
    d = _mk(marker=True)
    _write(state, d)
    b = sa.SourceAttribution()
    b._ensure_loaded()
    assert _disk_flags(state) == {KEY: True}


def test_rebuild_off_still_reads_flags_from_rolling_disk(monkeypatch, tmp_path):
    """回滚开关关掉时，标志来自磁盘（修复前为空 ⇒ 直接退回"盲窗"）。"""
    state = tmp_path / "state.json"
    _write(state, _mk(marker=True))
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "false")

    a = sa.SourceAttribution()
    assert a.exit_channel_shadow("trend_broken", "mid") is True
    assert bool((a._breaker_shadow or {}).get(KEY)) is True


# ── ② 空态合并分支 ──────────────────────────────────────────────────────────

def test_empty_state_merge_keeps_rolling_flags(monkeypatch, tmp_path):
    """空内存实例触发多进程防护：滚动口径磁盘的标志必须合并进内存、且不被写空。"""
    state = tmp_path / "state.json"
    _write(state, _mk(marker=True))
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))

    a = sa.SourceAttribution()          # 故意不调 _ensure_loaded
    a._maybe_save(force=True)           # 首次：触发合并分支（只读不写）
    assert bool((a._breaker_shadow or {}).get(KEY)) is True, "合并分支把滚动窗标志清零了"
    a._maybe_save(force=True)           # 二次：正常保存
    assert _disk_flags(state) == {KEY: True}, "一次空态保存就把磁盘标志写成空"


def test_empty_state_merge_drops_legacy_flags(monkeypatch, tmp_path):
    """旧口径文件仍按迁移语义丢弃标志（不得把累计制标志回灌）。"""
    state = tmp_path / "state.json"
    _write(state, _mk(marker=False))
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))

    a = sa.SourceAttribution()
    a._maybe_save(force=True)
    assert a._breaker_shadow == {}, "旧累计制标志被回灌进内存"
    assert a._shadow == {}


# ── ③ 落盘一致性 ────────────────────────────────────────────────────────────

def test_save_payload_carries_shadow_mode_marker(monkeypatch, tmp_path):
    """任何一次保存都要带口径标记（否则下次加载又会被当成旧文件剥离）。"""
    state = tmp_path / "state.json"
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "false")
    a = sa.SourceAttribution()
    a.tag_position(1, source="mlto", nature="swing", symbol="BTC")
    a._maybe_save(force=True)
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d.get(sa.SHADOW_MODE_KEY) == sa.SHADOW_MODE_ROLLING
    assert d.get("breaker_shadow") == {}


def test_rebuild_on_load_persists_flags(monkeypatch, tmp_path):
    """加载期重建出的标志要落盘（磁盘与内存一致）；已一致时不再写。"""
    state = tmp_path / "state.json"
    _write(state, {**_mk(marker=True, flags=False)})     # 有 breaker 窗但无标志
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "true")
    monkeypatch.setattr(sa, "_under_pytest", lambda: False)   # 模拟生产进程

    a = sa.SourceAttribution()
    a._ensure_loaded()
    assert bool((a._breaker_shadow or {}).get(KEY)) is True
    assert _disk_flags(state) == {KEY: True}, "重建结果没有落盘 ⇒ 回滚开关一关就退回盲窗"

    first_mtime = state.stat().st_mtime_ns
    b = sa.SourceAttribution()
    b._ensure_loaded()
    assert state.stat().st_mtime_ns == first_mtime, "磁盘已一致却仍在写盘"


def test_under_pytest_guard_is_wired(monkeypatch, tmp_path):
    """pytest 进程不得写生产状态文件（本文件自己就在 pytest 里跑）。"""
    assert sa._under_pytest() is True
    state = tmp_path / "state.json"
    _write(state, _mk(marker=True, flags=False))
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "true")
    a = sa.SourceAttribution()
    a._ensure_loaded()
    assert _disk_flags(state) == {}, "pytest 进程把生产状态文件写了（脏写纪律 §57/§75）"


# ── 源码护栏（防回退） ──────────────────────────────────────────────────────

def test_source_credit_shadow_still_not_persisted(monkeypatch, tmp_path):
    """**边界测试**：本次修复只让**熔断标志**跨重启；来源信用 shadow 仍不跨重启。

    依据既有契约（`test_source_attribution.py::test_persistence_roundtrip`）：
    `_shadow` 重启后清零、放行一笔，由一笔新平仓凭持久化的 `recent` 立即重建。
    修复若把 `_shadow` 也持久化，风险闸会被静默收紧（回归时该契约变红过一次）。
    """
    state = tmp_path / "state.json"
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "false")
    monkeypatch.setenv("EXIT_CHANNEL_SHADOW_MIN_N", "15")
    a = sa.SourceAttribution()
    for i in range(20):
        a.tag_position(7000 + i, source="factor", nature="scalp", symbol="SOL")
        a.record_close(7000 + i, pnl=-1.0, fee=0.0, close_reason="sl",
                       tier="short", symbol="SOL", nature="scalp")
    assert a.credit("factor", "scalp", "SOL") == 0.0
    assert bool((a._breaker_shadow or {}).get("short|sl")) is True
    a._maybe_save(force=True)

    b = sa.SourceAttribution()
    b._ensure_loaded()
    assert b.credit("factor", "scalp", "SOL") == 1.0, "来源信用 shadow 变成了跨重启（超出本次修复范围）"
    assert bool((b._breaker_shadow or {}).get("short|sl")) is True, "熔断标志反而没跨重启"
    b.tag_position(7100, source="factor", nature="scalp", symbol="SOL")
    b.record_close(7100, pnl=-1.0, fee=0.0, close_reason="sl",
                   tier="short", symbol="SOL", nature="scalp")
    assert b.credit("factor", "scalp", "SOL") == 0.0, "一笔新平仓应凭历史样本立即重建来源信用 shadow"


def test_source_guards_against_regression():
    import inspect
    src = inspect.getsource(sa.SourceAttribution._ensure_loaded)
    assert "SHADOW_MODE_ROLLING" in src, "加载路径缺少口径标记判定"
    assert "SHADOW_MODE_KEY" in src
    save_src = inspect.getsource(sa.SourceAttribution._maybe_save)
    assert "SHADOW_MODE_KEY" in save_src, "保存载荷缺口径标记 ⇒ 下次加载会误判为旧文件"
    assert "_disk_legacy" in save_src, "空态合并分支没有按磁盘口径决定是否丢弃标志"
    assert "logger.warning" in src, "加载期回写失败必须可见（原为 debug 静默）"
