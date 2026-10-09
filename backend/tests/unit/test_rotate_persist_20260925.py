# -*- coding: utf-8 -*-
"""[新目标·调度 R2] 轮转游标持久化的单测。

背景：`_ROTATE_STATE` 是模块级内存态，后端重启清零 ⇒ 轮转从头、尾部标的重新挨饿；
本会话内多次重启把健康检查 8~11 分钟的规律节奏打断成 26/62 分钟。
修复：`_save_rotate_cursor/_load_rotate_cursor` 落盘 `data/v3_factor_rotate_state.json`（路径可配），
开关 `V3_FACTOR_ROTATE_PERSIST`（默认 true），回滚 false。
"""
from __future__ import annotations

import pytest

import backend.services.full_auto.v3_factor_pipeline as V


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.delenv("V3_FACTOR_ROTATE_PERSIST", raising=False)
    monkeypatch.setenv("V3_FACTOR_ROTATE_STATE_PATH", str(tmp_path / "rot.json"))
    V._ROTATE_STATE["cursor"] = 0
    yield
    V._ROTATE_STATE["cursor"] = 0


def test_switch_default_on_and_rollback(monkeypatch):
    assert V._rotate_persist_enabled() is True
    monkeypatch.setenv("V3_FACTOR_ROTATE_PERSIST", "false")
    assert V._rotate_persist_enabled() is False
    monkeypatch.setenv("V3_FACTOR_ROTATE_PERSIST", "garbage")
    assert V._rotate_persist_enabled() is False  # 非法值 fail-closed（与本仓同约定）


def test_save_and_load_roundtrip():
    V._ROTATE_STATE["cursor"] = 7
    V._save_rotate_cursor()
    V._ROTATE_STATE["cursor"] = 0       # 模拟重启清零
    V._load_rotate_cursor()
    assert V._ROTATE_STATE["cursor"] == 7


def test_advance_persists():
    V._ROTATE_STATE["cursor"] = 0
    V._advance_rotate_cursor(total=30, computed=19)
    assert V._ROTATE_STATE["cursor"] == 19
    V._ROTATE_STATE["cursor"] = 0       # 模拟重启
    V._load_rotate_cursor()
    assert V._ROTATE_STATE["cursor"] == 19


def test_load_absent_file_is_noop():
    V._load_rotate_cursor()  # 文件不存在 ⇒ 不抛、游标不变
    assert V._ROTATE_STATE["cursor"] == 0


def test_disabled_does_not_persist(monkeypatch):
    monkeypatch.setenv("V3_FACTOR_ROTATE_PERSIST", "false")
    V._ROTATE_STATE["cursor"] = 5
    V._advance_rotate_cursor(total=30, computed=3)
    assert V._ROTATE_STATE["cursor"] == 8
    V._ROTATE_STATE["cursor"] = 0
    V._load_rotate_cursor()
    assert V._ROTATE_STATE["cursor"] == 0  # 关了就不该恢复
