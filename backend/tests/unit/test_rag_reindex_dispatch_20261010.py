# -*- coding: utf-8 -*-
"""[2026-10-10 学习进化前端升级] `/api/rag/reindex` 的分派契约。

现场缺口：前端的「重建索引」按钮会把 `source_type` 透传给后端，而
`index_from_db` 只认 5 个**数据库源**（`_build_index_payload`）；`knowledge_cards`
是**静态文件**源 ⇒ 此前落到「未知源类型」分支、静默返回 0，
前端显示"重建成功但没有文档"，而实际一张卡都没进索引 ✗。

本测试锁定：
  1. `knowledge_cards` 必须走 `index_knowledge_cards()`，并回带文件张数；
  2. 未知 `source_type` 必须带 `warning`（静默 0 分不清"源里没数据"与"源名不认识"）；
  3. 空 `source_type` 仍是全量重建（行为不变）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api import rag_routes as RR  # noqa: E402
from backend.services.rag_knowledge_service import COLL_KNOWLEDGE_CARDS  # noqa: E402


class _Svc:
    """替身：记录调用并返回可控计数。"""

    def __init__(self):
        self.is_ready = True
        self.calls = []

    def index_knowledge_cards(self):
        self.calls.append("cards")
        return 36

    def load_card_file(self):
        return [{"id": f"kb-{i}"} for i in range(36)]

    def index_from_db(self, db, source_type, incremental=False):
        self.calls.append(("db", source_type))
        return 5 if source_type == "strategy_lessons" else 0

    def full_reindex(self, db):
        self.calls.append("full")
        return {"knowledge_cards": 36}


@pytest.fixture
def svc(monkeypatch):
    s = _Svc()
    monkeypatch.setattr(
        "backend.services.rag_knowledge_service.rag_knowledge_service", s)
    return s


def test_reindex_knowledge_cards_uses_file_indexer(svc):
    out = RR.rag_reindex(RR.RAGReindexRequest(source_type=COLL_KNOWLEDGE_CARDS), db=None)
    assert svc.calls == ["cards"], "必须走 index_knowledge_cards（静态文件源）"
    assert out["indexed"] == 36
    assert out["file_count"] == 36, "回带文件张数：前端要能对比 文件 vs 索引"
    assert out["source_type"] == "knowledge_cards"


def test_reindex_unknown_source_warns(svc):
    out = RR.rag_reindex(RR.RAGReindexRequest(source_type="not_a_source"), db=None)
    assert out["indexed"] == 0
    assert out.get("warning") == "unknown_source_type_or_empty", (
        "静默 0 会被读成'重建成功但没数据'——必须显式区分")


def test_reindex_known_db_source_has_no_warning(svc):
    out = RR.rag_reindex(RR.RAGReindexRequest(source_type="strategy_lessons"), db=None)
    assert out["indexed"] == 5 and "warning" not in out


def test_reindex_empty_source_still_full(svc):
    out = RR.rag_reindex(RR.RAGReindexRequest(source_type=None), db=None)
    assert out["full_reindex"] is True
    assert svc.calls == ["full"], "行为不变：空 source_type 仍是全量重建"


def test_reindex_not_ready_raises_503(monkeypatch):
    s = _Svc()
    s.is_ready = False
    monkeypatch.setattr(
        "backend.services.rag_knowledge_service.rag_knowledge_service", s)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        RR.rag_reindex(RR.RAGReindexRequest(source_type=COLL_KNOWLEDGE_CARDS), db=None)
    assert ei.value.status_code == 503
