"""Bridge trading lessons into QAA 3.1 semantic memory.

This module keeps the existing StrategyMemory flow intact and adds a small,
durable RAG layer backed by the embedded QAA package. It is intentionally
best-effort: trading and close paths must never fail because memory sync fails.
"""

from __future__ import annotations

import logging
import os
import sys
import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_DOMAIN = "trading"
_LOCK = Lock()
_RAG = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_qaa_path() -> None:
    qaa_dir = str(_project_root() / "qaa_architecture_package")
    if qaa_dir not in sys.path:
        sys.path.insert(0, qaa_dir)


def _get_rag():
    """Create a process-wide persistent RAG pipeline for trading lessons."""
    global _RAG
    if _RAG is not None:
        return _RAG
    with _LOCK:
        if _RAG is not None:
            return _RAG
        _ensure_qaa_path()
        from qaa.knowledge.factory import create_embedder
        from qaa.knowledge.rag import RAGPipeline

        scope = os.getenv("QAA_KNOWLEDGE_SCOPE", "alpha-arena")
        # 嵌入 provider 由 QAA_EMBEDDING_BACKEND 决定（默认 hash，等价旧行为；可切 neural）
        embedder = create_embedder()

        # 知识库后端：默认 jsonl（保留原 trading_lessons.jsonl 路径与数据），
        # 设 QAA_KNOWLEDGE_BACKEND=chroma 时改用 ChromaDB HNSW（失败自动回退 jsonl）。
        kb_backend = os.getenv("QAA_KNOWLEDGE_BACKEND", "jsonl").strip().lower()
        store = None
        if kb_backend == "chroma":
            try:
                from qaa.knowledge.stores.chroma import ChromaKnowledgeStore

                chroma_dir = os.getenv(
                    "QAA_CHROMA_DIR", str(_project_root() / "data" / "qaa_chromadb")
                )
                store = ChromaKnowledgeStore(path=chroma_dir, scope=scope)
                logger.info("[QAA bridge] 交易记忆使用 ChromaDB 后端: %s (scope=%s)", chroma_dir, scope)
            except Exception as exc:  # noqa: BLE001 —— 初始化失败回退，绝不阻断交易
                logger.warning("[QAA bridge] Chroma 初始化失败，回退 jsonl: %s", exc)
                store = None
        if store is None:
            from qaa.knowledge.stores.jsonl import JsonlKnowledgeStore

            data_dir = Path(
                os.getenv("QAA_KNOWLEDGE_DIR", str(_project_root() / "data" / "qaa_knowledge"))
            )
            data_dir.mkdir(parents=True, exist_ok=True)
            store = JsonlKnowledgeStore(
                path=str(data_dir / "trading_lessons.jsonl"),
                scope=scope,
            )

        _RAG = RAGPipeline(store=store, embedder=embedder)
        return _RAG


def ingest_trade_lesson(
    *,
    lesson: str,
    symbol: str = "",
    side: str = "",
    pnl: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    exit_reason: str = "",
    strategy_id: str = "",
    tier: str = "",
    trade_nature: str = "",
    regime: str = "",
    source: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Persist one trading lesson into QAA semantic memory."""
    text = (lesson or "").strip()
    if not text:
        return []
    sym = (symbol or "").upper()
    meta: Dict[str, Any] = {
        "symbol": sym,
        "side": side or "",
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason or "",
        "strategy_id": strategy_id or "",
        "tier": tier or "",
        "trade_nature": trade_nature or "",
        "regime": regime or "",
    }
    meta.update(metadata or {})
    normalized = (
        f"symbol={sym}; side={side or '?'}; pnl={pnl}; pnl_pct={pnl_pct}; "
        f"exit_reason={exit_reason or '?'}; tier={tier or '?'}; "
        f"nature={trade_nature or '?'}; regime={regime or '?'}; lesson={text}"
    )
    try:
        return _get_rag().ingest_text(
            normalized,
            domain=_DOMAIN,
            source=source or f"{strategy_id or 'trade'}:{sym}:{exit_reason or 'close'}",
            source_type="trade_lesson",
            metadata=meta,
        )
    except Exception as exc:
        logger.debug("[QAATradeMemory] lesson ingest skipped: %s", exc)
        return []


def ingest_trade_outcome(outcome: Any, *, source: str = "") -> List[str]:
    """Persist a unified TradeOutcome into QAA semantic memory.

    This is the whole-learning-system entry: live/paper/backtest outcomes all
    pass through ``UnifiedLearningService.process_outcome`` and can be stored
    here without each caller knowing about QAA.
    """
    try:
        meta = getattr(outcome, "metadata", None)
        if not isinstance(meta, dict):
            meta = {}
        symbol = (getattr(outcome, "symbol", "") or "").upper()
        pnl = float(getattr(outcome, "pnl", 0.0) or 0.0)
        pnl_pct = float(getattr(outcome, "pnl_pct", 0.0) or 0.0)
        trade_nature = (
            getattr(outcome, "trade_nature", "")
            or {"short": "scalp", "mid": "swing", "long": "position"}.get(
                getattr(outcome, "tier", ""), ""
            )
        )
        exit_reason = (
            getattr(outcome, "exit_channel", "")
            or meta.get("close_reason")
            or meta.get("exit_reason")
            or ""
        )
        regime = getattr(outcome, "regime_at_entry", "") or getattr(outcome, "regime_at_exit", "")
        side = getattr(outcome, "side", "") or ""
        confidence = float(getattr(outcome, "confidence", 0.0) or 0.0)
        duration_seconds = int(getattr(outcome, "duration_seconds", 0) or 0)
        retention_ratio = getattr(outcome, "retention_ratio", None)
        peak_pnl_pct = float(getattr(outcome, "peak_pnl_pct", 0.0) or 0.0)

        result_label = "盈利" if pnl > 0 else "亏损" if pnl < 0 else "保本"
        lesson_hint = (
            "保持此类条件组合，但继续检查回撤保护。"
            if pnl > 0
            else "复盘方向判断、入场时机、止损距离、仓位和持仓时间。"
        )
        text = (
            f"source={getattr(outcome, 'source', '')}; strategy={getattr(outcome, 'strategy_id', '')}; "
            f"symbol={symbol}; side={side}; nature={trade_nature}; regime={regime}; "
            f"pnl={pnl:.4f}; pnl_pct={pnl_pct:.4f}; result={result_label}; "
            f"exit_reason={exit_reason or '?'}; confidence={confidence:.2f}; "
            f"duration_seconds={duration_seconds}; peak_pnl_pct={peak_pnl_pct:.4f}; "
            f"retention_ratio={retention_ratio}; lesson={lesson_hint}"
        )
        metadata = {
            "kind": "trade_outcome",
            "source": getattr(outcome, "source", ""),
            "strategy_id": getattr(outcome, "strategy_id", ""),
            "template_id": getattr(outcome, "template_id", ""),
            "symbol": symbol,
            "side": side,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
            "tier": getattr(outcome, "tier", ""),
            "trade_nature": trade_nature,
            "regime": regime,
            "confidence": confidence,
            "duration_seconds": duration_seconds,
            "peak_pnl_pct": peak_pnl_pct,
            "retention_ratio": retention_ratio,
            "partial_close": bool(meta.get("partial_close", False)),
        }
        return _get_rag().ingest_text(
            text,
            domain=_DOMAIN,
            source=source
            or (
                f"outcome:{getattr(outcome, 'source', 'unknown')}:"
                f"{getattr(outcome, 'strategy_id', '')}:{symbol}:{exit_reason or 'close'}"
            ),
            source_type="trade_outcome",
            metadata=metadata,
        )
    except Exception as exc:
        logger.debug("[QAATradeMemory] outcome ingest skipped: %s", exc)
        return []


def ingest_learning_artifact(
    *,
    artifact_type: str,
    text: str = "",
    payload: Optional[Dict[str, Any]] = None,
    strategy_id: str = "",
    symbol: str = "",
    regime: str = "",
    trade_nature: str = "",
    source: str = "",
) -> List[str]:
    """Persist a higher-level learning report/template into QAA semantic memory."""
    payload = dict(payload or {})
    body = (text or "").strip()
    if not body and payload:
        try:
            body = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            body = str(payload)
    if not body:
        return []

    sym = (symbol or payload.get("symbol") or payload.get("best_symbol") or "").upper()
    reg = regime or payload.get("regime") or payload.get("best_regime") or ""
    nature = trade_nature or payload.get("trade_nature") or payload.get("best_nature") or ""
    sid = strategy_id or payload.get("strategy_id") or ""
    normalized = (
        f"artifact={artifact_type}; strategy={sid}; symbol={sym}; "
        f"regime={reg}; nature={nature}; content={body}"
    )
    metadata = {
        "kind": artifact_type,
        "strategy_id": sid,
        "symbol": sym,
        "regime": reg,
        "trade_nature": nature,
    }
    for key in (
        "total_records",
        "overall_win_rate",
        "win_rate",
        "total_trades",
        "best_regime",
        "best_nature",
        "avg_pnl_per_trade",
    ):
        if key in payload:
            metadata[key] = payload[key]
    try:
        return _get_rag().ingest_text(
            normalized,
            domain=_DOMAIN,
            source=source or f"artifact:{artifact_type}:{sid or sym or 'global'}",
            source_type="learning_artifact",
            metadata=metadata,
        )
    except Exception as exc:
        logger.debug("[QAATradeMemory] learning artifact ingest skipped: %s", exc)
        return []


def build_qaa_rag_lessons_section(
    *,
    symbols: Optional[Iterable[str]] = None,
    regime: str = "",
    trade_nature: str = "",
    limit: int = 5,
) -> str:
    """Retrieve QAA semantic lessons and format them for the trading prompt."""
    sym_list = [str(s).upper() for s in (symbols or []) if s]
    query_parts = ["交易教训", "亏损复盘", "止损", "入场", "仓位"]
    query_parts.extend(sym_list)
    if regime:
        query_parts.append(f"regime {regime}")
    if trade_nature:
        query_parts.append(f"trade_nature {trade_nature}")
    query_text = " ".join(query_parts)

    try:
        from qaa.knowledge.base import RetrievalQuery

        ctx = _get_rag().retrieve(
            RetrievalQuery(
                query_text=query_text,
                domain=_DOMAIN,
                top_k=max(1, limit),
                source_types=["trade_lesson", "trade_outcome", "learning_artifact"],
            )
        )
        if not ctx.hits:
            return ""

        lines = [
            "### QAA 3.1 语义记忆（RAG检索出的历史交易教训）",
            "> 这些是从历史平仓复盘中固化的长期记忆。若本轮决策违反这些教训，reasoning 必须说明新证据。",
        ]
        for hit in ctx.hits[:limit]:
            meta = hit.chunk.metadata or {}
            sym = meta.get("symbol") or "?"
            side = meta.get("side") or "?"
            reason = meta.get("exit_reason") or "?"
            text = hit.chunk.text
            lesson = text.split("lesson=", 1)[-1] if "lesson=" in text else text
            lesson = lesson.split("content=", 1)[-1] if "content=" in lesson else lesson
            if len(lesson) > 180:
                lesson = lesson[:177] + "..."
            lines.append(
                f"- [{sym} {side} / {reason} / score={hit.score:.2f}] {lesson}"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("[QAATradeMemory] RAG prompt section skipped: %s", exc)
        return ""


def get_qaa_trade_memory_stats() -> Dict[str, Any]:
    """Expose lightweight diagnostics for tests and /api/health."""
    configured = {
        "embedding_backend": os.getenv("QAA_EMBEDDING_BACKEND", "hash").strip().lower(),
        "embedding_model": os.getenv("QAA_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
        "knowledge_backend": os.getenv("QAA_KNOWLEDGE_BACKEND", "jsonl").strip().lower(),
        "chroma_dir": os.getenv("QAA_CHROMA_DIR", ""),
        "reranker_backend": os.getenv("QAA_RERANKER_BACKEND", "lexical").strip().lower(),
        "scope": os.getenv("QAA_KNOWLEDGE_SCOPE", "alpha-arena"),
    }
    try:
        rag = _get_rag()
        store_stats = rag.store.get_stats()
        embedder = getattr(rag, "embedder", None)
        embedder_cls = type(embedder).__name__ if embedder is not None else "unknown"
        degraded = bool(getattr(embedder, "_degraded", False))
        active_backend = "hash"
        if embedder_cls == "NeuralEmbeddingProvider" and not degraded:
            active_backend = "neural"
        elif embedder_cls == "HashEmbeddingProvider":
            active_backend = "hash"
        store_cls = type(rag.store).__name__
        return {
            **configured,
            "active_embedding_backend": active_backend,
            "active_embedder_class": embedder_cls,
            "embedder_degraded": degraded,
            "embedder_dimensions": getattr(embedder, "dimensions", None),
            "active_store_class": store_cls,
            "neural_active": active_backend == "neural",
            "chroma_active": store_cls == "ChromaKnowledgeStore",
            **store_stats,
        }
    except Exception as exc:
        return {**configured, "error": str(exc)}
