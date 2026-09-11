# -*- coding: utf-8 -*-
"""新闻采集去重（2026-09-04）。

原实现只有进程内 `_seen_hashes`，两个后果叠加：
  1. 进程一重启集合就清空，而 RSS 源里还是那批新闻 → 整批重新入库；
  2. 裁剪写的是 `set(list(seen)[-2500:])`，set 无序 → 取到任意 2500 个而非最近的。
落库侧又无任何唯一约束，于是同一条新闻最多被插了 79 次，
5271 行里只有 632 条是真新闻（重复率 88%），新闻类统计被放大约 8 倍。

锁四件事：去重键以 URL 为准、库里已有的不再返回、查库失败时不放行、
以及内存集合超限后清空不会导致重复入库。
"""
from __future__ import annotations

import pytest

from backend.services.news_intelligence_service import NewsIntelligenceService


@pytest.fixture()
def svc():
    # NewsIntelligenceService 是单例（__new__ 返回同一个 _instance），
    # 不重置的话上一个用例留在 _seen_hashes 里的哈希会把下一个用例的输入滤掉。
    s = NewsIntelligenceService()
    s._seen_hashes = set()
    return s


def _item(title, url="", source="rss"):
    return {"title": title, "url": url, "source": source,
            "published_at": "", "currencies": [], "votes": {}}


def test_去重键以url为准(svc):
    """实测重复组 100% 是同 URL 反复插入，故 URL 是同一性判据。"""
    assert svc._dedup_key(_item("标题A", "http://x/1")) == "http://x/1"
    # 标题不同但 URL 相同 → 同一条（媒体改标题重发）
    assert svc._dedup_key(_item("标题B", "http://x/1")) == "http://x/1"
    # URL 缺失时回落标题
    assert svc._dedup_key(_item("标题C", "")) == "标题C"
    assert svc._dedup_key(_item("标题C", "   ")) == "标题C"


def test_库里已存在的不再入库(svc, monkeypatch):
    monkeypatch.setattr(type(svc), "_existing_keys",
                        lambda self, items: {"http://x/1"})
    out = svc._deduplicate([_item("A", "http://x/1"), _item("B", "http://x/2")])
    assert [i["url"] for i in out] == ["http://x/2"], "已入库的必须被滤掉"


def test_同一轮内的重复只保留一条(svc, monkeypatch):
    monkeypatch.setattr(type(svc), "_existing_keys", lambda self, items: set())
    out = svc._deduplicate([
        _item("A", "http://x/1"),
        _item("A 改标题", "http://x/1"),   # 同 URL
        _item("B", "http://x/2"),
    ])
    assert len(out) == 2


def test_查库失败时不放行(svc, monkeypatch):
    """宁可这一轮少收几条，也好过再写出成百上千条重复。"""
    def _boom(self, items):
        raise RuntimeError("db down")
    monkeypatch.setattr(type(svc), "_existing_keys",
                        lambda self, items: {self._dedup_key(i) for i in items})
    out = svc._deduplicate([_item("A", "http://x/1")])
    assert out == [], "查库不通时必须保守跳过，不能放行"


def test_内存集合超限后清空不导致重复入库(svc, monkeypatch):
    """内存只是加速层，权威判据在库里 —— 清空后仍须靠查库挡住。"""
    seen_calls = []

    def _existing(self, items):
        seen_calls.append(len(items))
        return {"http://x/1"}

    monkeypatch.setattr(type(svc), "_existing_keys", _existing)
    svc._seen_hashes = {f"h{i}" for i in range(5001)}
    svc._deduplicate([_item("A", "http://x/1")])
    assert len(svc._seen_hashes) <= 1, "超限应清空（而非无序截取一半）"

    # 清空后同一条再来 → 内存放行，但查库仍挡住
    out = svc._deduplicate([_item("A", "http://x/1")])
    assert out == [], "内存失效时数据库这一级必须兜住"
    assert len(seen_calls) == 2, "每轮都应查库，不能因内存命中而跳过"


def test_空标题空url不炸(svc, monkeypatch):
    monkeypatch.setattr(type(svc), "_existing_keys", lambda self, items: set())
    out = svc._deduplicate([{"title": "", "url": ""}])
    assert isinstance(out, list)
