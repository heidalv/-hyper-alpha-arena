# -*- coding: utf-8 -*-
"""① 文献情报 agent —— 投喂/arXiv 监控 → 知识卡抽取（版权纪律：只存元数据+LLM 自写摘要）。"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Dict, List, Optional

from backend.services.factors_lab import common, config

logger = logging.getLogger(__name__)

_CARD_SYSTEM = (
    "你是量化因子研究的文献情报员。把给定的论文摘要提炼为知识卡 JSON："
    '{"title":"...","phenomenon":"现象(≤60字)","mechanism":"经济学机制(≤80字)",'
    '"testable_claims":["可检验命题1",...],"data_requirements":"数据需求(≤40字)",'
    '"applicable_regime":"trend|range|high_vol|any","horizon":"scalp|midlong|any",'
    '"quality":0.0-1.0}。只输出 JSON。'
)


def _dedup_key(title: str) -> str:
    return hashlib.md5((title or "").strip().lower().encode("utf-8")).hexdigest()[:12]


def _known_keys() -> set:
    return {c.get("dedup_key") for c in common.read_jsonl(config.knowledge_path()) if c.get("dedup_key")}


def feed(title: str, abstract: str, source: str = "manual", url: str = "",
         *, extract: bool = True) -> Dict[str, object]:
    """人工投喂入口：去重 → （可选）LLM 抽知识卡 → 落盘。返回结果卡。"""
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    if not title or len(abstract) < 30:
        return {"ok": False, "error": "title/abstract 过短（摘要≥30字）"}
    key = _dedup_key(title)
    if key in _known_keys():
        return {"ok": False, "error": "duplicate", "dedup_key": key}
    card: Dict[str, object] = {
        "dedup_key": key, "title": title, "source": source, "url": url,
        "abstract_len": len(abstract), "ts": time.time(),
    }
    if extract:
        raw = common.call_llm(_CARD_SYSTEM, f"标题：{title}\n摘要：{abstract[:3000]}",
                              caller="factors_lab_1", max_tokens=900)
        parsed = common.parse_json_block(raw or "")
        if isinstance(parsed, dict):
            card["card"] = {k: parsed.get(k) for k in
                            ("phenomenon", "mechanism", "testable_claims",
                             "data_requirements", "applicable_regime", "horizon", "quality")}
            card["extract_ok"] = True
        else:
            card["card"] = None
            card["extract_ok"] = False
            card["abstract_snippet"] = abstract[:200]  # 版权纪律：只留片段
    common.append_jsonl(config.knowledge_path(), card)
    logger.info("[FactorsLab①] 知识卡入卡 %s extract=%s (%s)", key, card.get("extract_ok"), source)
    return {"ok": True, **card}


def scan_arxiv(max_items: int = 8) -> Dict[str, object]:
    """arXiv q-fin 监控（标准库 urllib——后端运行时无 httpx/requests，失败静默）。"""
    try:
        import urllib.parse
        import urllib.request
        qs = urllib.parse.urlencode({
            "search_query": "cat:q-fin.TR OR cat:q-fin.PM",
            "sortBy": "submittedDate", "sortOrder": "descending", "max_results": max_items})
        req = urllib.request.Request(
            f"https://export.arxiv.org/api/query?{qs}",
            headers={"User-Agent": "factors_lab/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 白名单目标
            xml = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        logger.info("[FactorsLab①] arXiv 抓取失败（跳过本轮监控）: %s", str(e)[:120])
        return {"ok": False, "fetched": 0, "fed": 0, "error": str(e)[:120]}
    entries = xml.split("<entry>")[1:]
    fed, dup = 0, 0
    for e in entries:
        def _tag(t: str) -> str:
            s, i = e.find(f"<{t}>"), e.find(f"</{t}>")
            return e[s + len(t) + 2: i] if 0 <= s < i else ""
        # 简易抽取（title/summary 含 inner tags 少）
        title = _tag("title").replace("\n", " ").strip()
        summary = _tag("summary").replace("\n", " ").strip()
        link = (e.split('href="')[1].split('"')[0] if 'href="' in e else "")
        if not title or not summary:
            continue
        res = feed(title, summary, source="arxiv_qfin", url=link)
        if res.get("ok"):
            fed += 1
        else:
            dup += 1
    return {"ok": True, "fetched": len(entries), "fed": fed, "dup": dup}


_VEC_CACHE_PATH = config.data_dir() / "knowledge_vectors.json"


def _card_text(c: Dict[str, object]) -> str:
    card = c.get("card") or {}
    return " ".join(str(x) for x in (
        c.get("title"), card.get("phenomenon"), card.get("mechanism"),
        " ".join(card.get("testable_claims") or [])))


def _embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """复用 RAG 服务 bge 模型（懒加载；不可用返回 None → 词汇兜底）。"""
    try:
        from backend.services.rag_knowledge_service import rag_knowledge_service
        return rag_knowledge_service._embed_texts(texts)
    except Exception:
        return None


def _ensure_card_vectors(cards: List[Dict[str, object]]) -> Dict[str, List[float]]:
    """知识卡向量缓存（dedup_key → vec），增量补算。"""
    import json as _json

    cache: Dict[str, List[float]] = {}
    try:
        cache = _json.loads(_VEC_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    missing = [c for c in cards if c.get("dedup_key") and c["dedup_key"] not in cache]
    if missing:
        vecs = _embed_texts([_card_text(c) for c in missing])
        if vecs:
            for c, v in zip(missing, vecs):
                cache[c["dedup_key"]] = [float(x) for x in v]  # 全维存储（截断会破坏余弦语义）
            try:
                _VEC_CACHE_PATH.write_text(_json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        else:
            return {}
    # 只保留当前卡片键
    live = {str(c.get("dedup_key")) for c in cards}
    return {k: v for k, v in cache.items() if k in live}


def _cosine(a: List[float], b: List[float]) -> float:
    na, nb = len(a), len(b)
    n = min(na, nb)
    dot = sum(x * y for x, y in zip(a[:n], b[:n]))
    sa = sum(x * x for x in a[:n]) ** 0.5
    sb = sum(x * x for x in b[:n]) ** 0.5
    return dot / (sa * sb) if sa and sb else 0.0


def retrieve(query: str, k: int = 3) -> List[Dict[str, object]]:
    """top-k 知识卡：bge 嵌入余弦（主）→ 词汇（兜底）。"""
    cards = [c for c in common.read_jsonl(config.knowledge_path()) if c.get("extract_ok")]
    if not cards:
        return []
    vectors = _ensure_card_vectors(cards)
    if vectors:
        qv = (_embed_texts([query]) or [None])[0]
        if qv:
            scored = []
            for c in cards:
                v = vectors.get(str(c.get("dedup_key")))
                if v:
                    scored.append((_cosine(qv, v)
                                   + 0.001 * float((c.get("card") or {}).get("quality") or 0.5), c))
            scored.sort(key=lambda kv: -kv[0])
            return [c for _, c in scored[:k]]
    # 词汇兜底
    scored = []
    for c in cards:
        q = common.lexical_score(query, _card_text(c)) + 0.001 * float((c.get("card") or {}).get("quality") or 0.5)
        scored.append((q, c))
    scored.sort(key=lambda kv: -kv[0])
    return [c for _, c in scored[:k]]
