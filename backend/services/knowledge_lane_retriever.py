# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] 按车道检索同一本知识库。

用户设计原话："不另开库。就用现有 RAG，中线、长线、高频都查它。"

所以这里**不新建索引、不新建 store**：只是把 `rag_knowledge_service.retrieve()`
包一层，做三件它不做的事：

1. **按车道过滤**：卡片有 `lanes` 字段（`hft`/`mid`/`long`/`all`）。Chroma 的 `where`
   只支持等值匹配，不能在 `"hft,mid"` 这种串里找子串 ⇒ 先多取（`top_k*3`，至少 30）
   再在 Python 里过滤，最后截断。这样不会"因为过滤太晚而返回空"。
2. **三类一起给**：`knowledge_cards`（书/论文蒸馏，**参考**）+ `strategy_lessons`
   （我们自己的账，**事实**）+ `static_knowledge`（K 线/宏观）。三者必须在同一个
   结果里出现 —— 模型要能同时看到"书上怎么说"和"我们的账怎么打脸的"。
3. **可判定的降级**：RAG 没就绪 / degraded / 检索锁超时，一律返回
   `degraded=True` 与空集。调用方（`brain_advisor`）据此**拒绝改参数**，
   而不是拿空证据去让模型自由发挥。

诚实声明（必须写在返回值里，也必须被调用方尊重）：卡片是**参考，不是下单指令**；
与本仓账冲突时**以账为准**。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 车道允许看到的卡片 lane 标签
LANE_TAGS = ("hft", "mid", "long", "all")
DEFAULT_TOPK = 8
OVERFETCH = 3
MIN_FETCH = 30

CARD_COLLECTION = "knowledge_cards"
LESSON_COLLECTION = "strategy_lessons"
STATIC_COLLECTION = "static_knowledge"

# 与本仓账冲突时的口径（写进返回体，喂给模型）
CONFLICT_RULE = "本仓真实成交账与卡片冲突时，以账为准；卡片不构成下单指令。"

# 车道 → 检索用语（同一本库，但问题不同：高频问盘口机制，中长线问持仓与成本）
LANE_QUERY_HINT: Dict[str, str] = {
    "hft": "挂单做市 队列 价差 逆向选择 库存 手续费 maker taker 盘口流动性",
    "mid": "波段 持仓成本 止损 资金费 波动率 出场时机",
    "long": "趋势 基差 资金费 保证金 强平距离 持仓周期",
}


def _norm_lane(lane: str) -> str:
    l = str(lane or "").strip().lower()
    return l if l in LANE_TAGS else "mid"


def _rows(res: Dict[str, List[Dict[str, Any]]], coll: str) -> List[Dict[str, Any]]:
    out = res.get(coll) or []
    return out if isinstance(out, list) else []


def _lane_ok(meta: Dict[str, Any], lane: str) -> bool:
    """卡片/元数据是否对这条车道可见。缺 `lanes` 字段 ⇒ 视为 `all`（fail-open）。

    容忍三种输入：Chroma 的 metadata dict、`{"lanes": "hft,mid"}`、以及直接传
    `lanes` 字符串（测试与干跑用）。
    """
    if isinstance(meta, str):
        raw: Any = meta
    elif isinstance(meta, dict):
        raw = meta.get("lanes")
    else:
        raw = None
    if raw in (None, ""):
        return True
    tags = {t.strip().lower() for t in str(raw).split(",") if t.strip()}
    return (lane in tags) or ("all" in tags)


def _card_item(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {"card_id": "", "text": str(row or ""), "distance": 0.0,
                "source": "", "topics": ""}
    meta = row.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    dist = row.get("distance")
    return {
        "card_id": str(meta.get("card_id") or ""),
        "text": row.get("text") or "",
        "distance": float(dist) if dist is not None else 0.0,
        "source": str(meta.get("source") or ""),
        "topics": str(meta.get("topics") or ""),
    }


def _plain_item(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {"text": str(row or ""), "distance": 0.0, "strategy_id": "", "source": ""}
    meta = row.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    dist = row.get("distance")
    return {
        "text": row.get("text") or "",
        "distance": float(dist) if dist is not None else 0.0,
        "strategy_id": str(meta.get("strategy_id") or ""),
        "source": str(meta.get("source") or meta.get("symbol") or ""),
    }


def retrieve_for_lane(
    query: str,
    lane: str,
    *,
    top_k: int = DEFAULT_TOPK,
    service: Optional[Any] = None,
    cards: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """按车道检索知识卡片 + 本仓教训 + 静态知识。

    Returns:
        {
          "lane": "hft",
          "degraded": bool,          # True ⇒ 调用方必须放弃本轮参数改动
          "cards": [{card_id, text, distance, source, topics}],
          "lessons": [{text, strategy_id, source, distance}],
          "static": [{text, source, distance}],
          "card_ids": ["kb-..."],    # 供 prompt 里要求模型 `cited` 用
          "conflict_rule": "...",
          "reason": "" | "not_ready" | "degraded" | "exception:...",
        }
    """
    lane_key = _norm_lane(lane)
    q = f"{LANE_QUERY_HINT.get(lane_key, '')} {str(query or '')}".strip()
    out: Dict[str, Any] = {
        "lane": lane_key, "query": q, "degraded": False,
        "cards": [], "lessons": [], "static": [], "card_ids": [],
        "conflict_rule": CONFLICT_RULE, "reason": "",
    }
    if not q:
        return out

    svc = service
    if svc is None:
        try:
            from backend.services.rag_knowledge_service import rag_knowledge_service
            svc = rag_knowledge_service
        except Exception as e:  # noqa: BLE001
            out["degraded"] = True
            out["reason"] = f"exception:{e}"
            return out

    # RAG 是**延迟加载**：`is_ready` 在首次检索前恒为 False（`_ensure_ready()` 才真正
    # 去加载 Chroma + embedding 模型）。不主动触发就会把"还没用过"误判成"不可用"
    # ⇒ 主脑永远只能 hold。这里触发一次（加载失败会自己进 degraded / 可重试态）。
    if not getattr(svc, "is_ready", False) and hasattr(svc, "_ensure_ready"):
        try:
            svc._ensure_ready()
        except Exception as e:  # noqa: BLE001
            logger.warning("[kb-lane] RAG 初始化失败: %s", e)

    # 调用方可以直接给卡片（干跑/离线复核）：此时卡片本身不依赖 RAG，但仍然要
    # 尝试取"我们自己的教训/静态知识"——它们在同一个 RAG 里。RAG 不可用时如实降级。
    if cards is not None and not getattr(svc, "is_ready", False):
        out["cards"] = [
            {"card_id": str(c.get("id") or ""), "text": _card_text(c),
             "distance": 0.0, "source": str(c.get("source") or ""),
             "topics": ",".join(str(x) for x in (c.get("topics") or []))}
            for c in cards if _lane_ok(_card_meta(c), lane_key)
        ][: int(top_k)]
        out["card_ids"] = [c["card_id"] for c in out["cards"] if c["card_id"]]
        out["degraded"] = True
        out["reason"] = "not_ready"
        return out

    if not getattr(svc, "is_ready", False):
        out["degraded"] = True
        out["reason"] = "not_ready"
        return out
    if getattr(svc, "is_degraded", False):
        out["degraded"] = True
        out["reason"] = "degraded"
        return out

    # 卡片：优先用调用方给的（测试/无 Chroma 场景），否则走 RAG
    card_rows: List[Dict[str, Any]] = []
    try:
        if cards is not None:
            card_rows = [{"text": _card_text(c), "metadata": _card_meta(c), "distance": 0.0}
                         for c in cards
                         if _lane_ok(_card_meta(c), lane_key)]
        else:
            need = max(int(top_k) * OVERFETCH, MIN_FETCH)
            res = svc.retrieve(q, top_k=need, collections=[CARD_COLLECTION]) or {}
            card_rows = [r for r in _rows(res, CARD_COLLECTION)
                         if _lane_ok(r.get("metadata") or {}, lane_key)]
    except Exception as e:  # noqa: BLE001
        logger.warning("[kb-lane] 卡片检索失败: %s", e)
        card_rows = []

    try:
        res2 = svc.retrieve(
            q, top_k=max(int(top_k), MIN_FETCH),
            collections=[LESSON_COLLECTION, STATIC_COLLECTION]) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[kb-lane] 教训/静态检索失败: %s", e)
        out["degraded"] = True
        out["reason"] = f"exception:{e}"
        return out

    out["cards"] = [_card_item(r) for r in card_rows[: int(top_k)]]
    out["card_ids"] = [c["card_id"] for c in out["cards"] if c["card_id"]]
    out["lessons"] = [_plain_item(r) for r in _rows(res2, LESSON_COLLECTION)[: int(top_k)]]
    out["static"] = [_plain_item(r) for r in _rows(res2, STATIC_COLLECTION)[: int(top_k)]]
    if not out["cards"] and not out["lessons"]:
        out["reason"] = "no_hits"
    return out


def _card_text(card: Dict[str, Any]) -> str:
    from backend.services.rag_knowledge_service import RAGKnowledgeService
    return RAGKnowledgeService.card_document(card)


def _card_meta(card: Dict[str, Any]) -> Dict[str, Any]:
    lanes = card.get("lanes") or ["all"]
    if isinstance(lanes, str):
        lanes = [lanes]
    topics = card.get("topics") or []
    if isinstance(topics, str):
        topics = [topics]
    return {
        "kind": "kb_card",
        "card_id": str(card.get("id") or ""),
        "lanes": ",".join(str(x) for x in lanes),
        "topics": ",".join(str(x) for x in topics),
        "source": str(card.get("source") or ""),
    }
