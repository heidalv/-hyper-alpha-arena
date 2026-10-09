# -*- coding: utf-8 -*-
"""[续作 R3] context_pack 尾部 meta 标签澄清的单测。

背景（§59 实测）：`data_cutoff_ms` 来自 `context_pack.py:1189-1193`
`cutoff = min(now, market.as_of_ms + 1)`，而 `as_of_ms` 取自 `kl_1d[-1]`（最后一根**日线**，4h 兜底，L434-436）
⇒ 分辨率是**天**（当天恒为 00:00 UTC）。旧标签「数据截止（UTC ms）=…」是给模型看的，
会让人/模型误以为数据停在某个整点。

同时本轮全仓审计（backend + 前端 + 文档）确认：**没有任何消费者**用它算年龄或判新鲜度，
它只用于"同 pack 回放" ⇒ 本次只改标签、不改值、不改判据。
"""
from __future__ import annotations

import pytest

from backend.services.analysis.context_pack import ContextPack


def _pack():
    return ContextPack(task="t", data_cutoff_ms=1790208000001, layers={"market": {"a": 1}})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("CONTEXT_PACK_CUTOFF_LABEL_EXPLICIT", raising=False)
    yield


def test_default_label_is_explicit():
    t = _pack().to_prompt_text(10 ** 9)
    assert "日线口径" in t, "默认必须写明口径，避免把日线时间戳当成此刻"
    assert "1d K 线" in t


def test_label_keeps_the_same_value():
    """只改措辞，不得改数值。"""
    t = _pack().to_prompt_text(10 ** 9)
    assert "1790208000001" in t


def test_rollback_restores_old_label(monkeypatch):
    monkeypatch.setenv("CONTEXT_PACK_CUTOFF_LABEL_EXPLICIT", "false")
    t = _pack().to_prompt_text(10 ** 9)
    assert "日线口径" not in t
    assert "数据截止（UTC ms）=1790208000001" in t


def test_meta_stays_at_tail(monkeypatch):
    """前缀缓存契约：易变字段必须仍在**文本末尾**（本轮改动不得把它挪走）。

    判据用"结尾"而不是"占比"：小样本 pack 的 meta 尾串本身可能比正文还长，
    用长度比例会写出无意义的断言（本测试首版就是这么错的）。
    """
    t = _pack().to_prompt_text(10 ** 9)
    head, sep, tail = t.partition("__meta__")
    assert sep, "meta 段必须存在"
    assert tail.startswith("："), "标签格式：__meta__ 后应紧跟全角冒号"
    assert t.rstrip().endswith("缺失层/错误=无"), "meta 必须收尾"
    assert '{"market"' in head, "正文应在 meta 之前"
