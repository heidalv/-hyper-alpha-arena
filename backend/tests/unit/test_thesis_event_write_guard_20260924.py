# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R23] thesis_store.append_event 的离线探针零写入闸单测。

为什么需要：`append_event` 显式忽略调用方传入的 db，用独立短连接自行 commit
（thesis_store.py 注释：'db 参数保留兼容……避免 LLM 后传入连接已死导致事件丢失'）。
⇒ 用 SAVEPOINT + 回滚包裹的验证方法**对本表无效**，R22 的 A/B 脚本因此把
   thesis=th-1 的 2 条 postmortem + 1 条 owm_bump 泄漏进生产库（已清理）。
本闸给离线探针一个真正零写入的模式。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import thesis_store as TS


def test_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("MLTO_THESIS_EVENT_WRITE_DISABLED", raising=False)
    assert TS._event_write_disabled() is False


@pytest.mark.parametrize("val", ["true", "1", "YES", "on"])
def test_switch_on(monkeypatch, val):
    monkeypatch.setenv("MLTO_THESIS_EVENT_WRITE_DISABLED", val)
    assert TS._event_write_disabled() is True


@pytest.mark.parametrize("val", ["false", "0", "no", "off", "", "garbage"])
def test_switch_off_or_invalid(monkeypatch, val):
    monkeypatch.setenv("MLTO_THESIS_EVENT_WRITE_DISABLED", val)
    assert TS._event_write_disabled() is False


def test_append_event_writes_nothing_when_disabled(monkeypatch):
    """闸打开时：不得触碰数据库（把 AnalyticsSessionLocal 换成会炸的桩）。"""
    monkeypatch.setenv("MLTO_THESIS_EVENT_WRITE_DISABLED", "true")

    import backend.database.connection as conn

    class _Boom:
        def __call__(self, *a, **k):
            raise AssertionError("闸打开时不应创建 DB 会话")

    monkeypatch.setattr(conn, "AnalyticsSessionLocal", _Boom())
    # 不应抛异常，也不应写库
    TS.append_event("th-guard-test", "postmortem", {"pnl": 1.0})


def test_append_event_noop_without_thesis_id(monkeypatch):
    monkeypatch.delenv("MLTO_THESIS_EVENT_WRITE_DISABLED", raising=False)
    import backend.database.connection as conn

    class _Boom:
        def __call__(self, *a, **k):
            raise AssertionError("thesis_id 为空时不应创建 DB 会话")

    monkeypatch.setattr(conn, "AnalyticsSessionLocal", _Boom())
    TS.append_event("", "postmortem", {"pnl": 1.0})
