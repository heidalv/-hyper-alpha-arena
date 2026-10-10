# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] 同一本知识库的按车道检索契约。

锁定的三件事：

1. **不另开库**：只包一层现有 `rag_knowledge_service.retrieve()`（collection 名字必须
   是既有的 `knowledge_cards` / `strategy_lessons` / `static_knowledge`）。
2. **降级必须可判定**：RAG 未就绪 / degraded / 检索抛异常 ⇒ `degraded=True`。
   调用方（`brain_advisor`）据此**拒绝改参数**。若这里 fail-open 返回空集而不标记，
   模型会在"没有任何依据"的情况下自由发挥 —— 那正是这套设计要防的。
3. **卡片按车道可见**：`lanes` 是逗号串（Chroma 的 where 只能等值匹配，过滤只能在
   Python 侧做），`all` 对所有车道可见，缺字段 fail-open。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services import knowledge_lane_retriever as KB  # noqa: E402
from backend.services.rag_knowledge_service import (  # noqa: E402
    ALL_COLLECTIONS,
    COLL_KNOWLEDGE_CARDS,
    RAGKnowledgeService,
)


CARD_HFT = {
    "id": "kb-test-hft-01", "title": "挂单与吃单的结构差别", "lanes": ["hft"],
    "topics": ["orderbook", "maker_taker"],
    "claim": "成交是「谁先挂单谁收钱」的结构问题：挂单提供流动性、吃单消费流动性。",
    "use_when": "判断该挂单还是该追价时。",
    "not_when": "不能推出某个价位一定能成交。",
    "source": "测试用卡（非真实引用）", "verified_in_repo": "本仓实测：Aster 挂单 0 费、吃单 4bp。",
}
CARD_ALL = {
    "id": "kb-test-all-01", "title": "止损不等于强平", "lanes": ["all"],
    "topics": ["liquidation", "risk"],
    "claim": "强平按破产价加保险基金成交，实际损失不低于维持保证金且常伴滑点。",
    "use_when": "评估杠杆与强平距离时。", "not_when": "不适用于现货。",
    "source": "测试用卡（非真实引用）", "verified_in_repo": "",
}
CARD_MID = {
    "id": "kb-test-mid-01", "title": "时间止损", "lanes": ["mid"],
    "topics": ["holding"], "claim": "论点未兑现就该平仓，因为每天都在付资金费与机会成本。",
    "use_when": "波段持仓超期时。", "not_when": "不适用于秒级来回。",
    "source": "测试用卡（非真实引用）", "verified_in_repo": "",
}


class FakeRag:
    """按 collection 名回预置行；同时暴露 is_ready / is_degraded 供降级分支用。"""

    def __init__(self, rows=None, ready=True, degraded=False, boom=False):
        self.rows = rows or {}
        self.is_ready = ready
        self.is_degraded = degraded
        self.boom = boom
        self.calls = []

    def retrieve(self, query_text, top_k=5, collections=None, metadata_filter=None):
        self.calls.append((query_text, top_k, tuple(collections or ())))
        if self.boom:
            raise RuntimeError("chroma down")
        return {c: list(self.rows.get(c) or [])[:top_k] for c in (collections or ())}


def _row(text, **meta):
    return {"text": text, "distance": 0.1, "metadata": meta}


# ══ 1. collection 名与既有结构 ═════════════════════════════════════════
def test_collection_names_match_existing_rag():
    assert COLL_KNOWLEDGE_CARDS == "knowledge_cards"
    assert COLL_KNOWLEDGE_CARDS in ALL_COLLECTIONS, "必须并入既有 5 个 collection"
    assert KB.LESSON_COLLECTION == "strategy_lessons"
    assert KB.STATIC_COLLECTION == "static_knowledge"
    # 只读现有 RAG：本模块不定义任何 collection 常量之外的新库
    assert "chromadb" not in Path(KB.__file__).read_text(encoding="utf-8")


# ══ 2. 卡片文档文本 ════════════════════════════════════════════════════
def test_card_document_contains_id_and_not_when():
    doc = RAGKnowledgeService.card_document(CARD_HFT)
    assert "kb-test-hft-01" in doc
    assert "何时不适用" in doc, "没有「什么时候不能用」的卡片正是历史上最贵的错误"
    assert "吃单消费流动性" in doc


def test_card_document_tolerates_missing_fields():
    doc = RAGKnowledgeService.card_document({"id": "kb-x"})
    assert "kb-x" in doc and isinstance(doc, str)


def test_load_card_file_reads_backend_data():
    cards = RAGKnowledgeService.load_card_file()
    assert isinstance(cards, list)
    # 真文件必须每张卡都有 id（否则根本不该进索引）
    for c in cards[:200]:
        assert c.get("id")


# ══ 3. 车道过滤 ════════════════════════════════════════════════════════
def test_lane_filter_keeps_matching_and_all_cards():
    # 传一个空 FakeRag：本用例只验车道过滤，不让"真实 RAG 是否就绪"影响结果
    r = KB.retrieve_for_lane("挂单 吃单 费率", "hft", service=FakeRag(),
                             cards=[CARD_HFT, CARD_ALL, CARD_MID])
    ids = [c["card_id"] for c in r["cards"]]
    assert "kb-test-hft-01" in ids
    assert "kb-test-all-01" in ids, "lanes=all 对所有车道可见"
    assert "kb-test-mid-01" not in ids, "中线的卡片不该出现在高频的检索结果里"
    assert r["degraded"] is False


def test_lane_filter_applies_to_rag_rows():
    rag = FakeRag({COLL_KNOWLEDGE_CARDS: [
        _row("卡片A", kind="kb_card", card_id="kb-a", lanes="hft"),
        _row("卡片B", kind="kb_card", card_id="kb-b", lanes="long"),
    ]})
    r = KB.retrieve_for_lane("价差", "hft", service=rag)
    assert [c["card_id"] for c in r["cards"]] == ["kb-a"]
    assert r["card_ids"] == ["kb-a"]


def test_missing_lanes_field_is_visible_everywhere():
    rag = FakeRag({COLL_KNOWLEDGE_CARDS: [_row("无标签卡", kind="kb_card", card_id="kb-no-lane")]})
    for lane in ("hft", "mid", "long"):
        r = KB.retrieve_for_lane("x", lane, service=rag)
        assert [c["card_id"] for c in r["cards"]] == ["kb-no-lane"]


def test_unknown_lane_falls_back_to_mid():
    r = KB.retrieve_for_lane("x", "nonsense", service=FakeRag(), cards=[CARD_HFT, CARD_MID])
    assert r["lane"] == "mid"
    assert [c["card_id"] for c in r["cards"]] == ["kb-test-mid-01"]


def test_explicit_cards_without_rag_still_degrade():
    """给了卡片但 RAG 未就绪 ⇒ 卡片能用、但必须标降级（我们自己的教训拿不到）。

    生产里 `brain_advisor` 依赖这个标记**拒绝改参数**：只有书上怎么说、
    完全没有我们自己的账，不足以支持一次参数变更。
    """
    r = KB.retrieve_for_lane("x", "hft", service=FakeRag(ready=False), cards=[CARD_HFT])
    assert [c["card_id"] for c in r["cards"]] == ["kb-test-hft-01"]
    assert r["degraded"] is True and r["reason"] == "not_ready"
    assert r["lessons"] == []


# ══ 4. 三类结果一起给 ══════════════════════════════════════════════════
def test_lessons_and_static_are_returned_alongside_cards():
    rag = FakeRag({
        KB.LESSON_COLLECTION: [_row("策略教训: ETH 挂单被吃 3bp", strategy_id="mm_asterdex_pp")],
        KB.STATIC_COLLECTION: [_row("K线形态: 长上影")],
    })
    r = KB.retrieve_for_lane("挂单被吃", "hft", service=rag, cards=[CARD_HFT])
    assert r["lessons"] and r["lessons"][0]["strategy_id"] == "mm_asterdex_pp"
    assert r["static"], "静态知识（K线/宏观）也要一起给"
    assert r["conflict_rule"].startswith("本仓真实成交账")


def test_query_hint_differs_per_lane():
    r1 = KB.retrieve_for_lane("", "hft", service=FakeRag())
    assert r1["cards"] == [] and r1["degraded"] is False
    rag = FakeRag()
    KB.retrieve_for_lane("持仓", "hft", service=rag)
    KB.retrieve_for_lane("持仓", "long", service=rag)
    q_hft = rag.calls[0][0]
    q_long = rag.calls[-1][0]
    assert q_hft != q_long and "逆向选择" in q_hft and "基差" in q_long


# ══ 5. 降级必须可判定 ══════════════════════════════════════════════════
def test_not_ready_marks_degraded():
    r = KB.retrieve_for_lane("x", "hft", service=FakeRag(ready=False))
    assert r["degraded"] is True and r["reason"] == "not_ready"
    assert r["cards"] == [] and r["card_ids"] == []


def test_embed_degraded_marks_degraded():
    r = KB.retrieve_for_lane("x", "hft", service=FakeRag(degraded=True))
    assert r["degraded"] is True and r["reason"] == "degraded"


def test_retrieve_exception_marks_degraded():
    r = KB.retrieve_for_lane("x", "hft", service=FakeRag(boom=True))
    assert r["degraded"] is True and r["reason"].startswith("exception:")


def test_no_hits_is_not_degraded_but_is_labelled():
    """检索成功但没有命中 ⇒ 不是降级，但必须让调用方看得见（reason=no_hits）。"""
    r = KB.retrieve_for_lane("x", "hft", service=FakeRag(), cards=[])
    assert r["degraded"] is False and r["reason"] == "no_hits"
