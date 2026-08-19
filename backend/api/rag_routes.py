"""
RAG 知识库管理 API

端点：
  GET  /api/rag/stats     — 各 collection 文档数 / 最后索引时间
  GET  /api/rag/health    — 服务状态（模型加载 / DB 连接）
  POST /api/rag/search    — 手动测试语义检索
  POST /api/rag/reindex   — 触发全量重建索引
  POST /api/rag/init      — 手动触发首次初始化（加载模型 + 全量索引）
"""

import logging
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["RAG Knowledge Base"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ------------------------------------------------------------------
#  请求/响应模型
# ------------------------------------------------------------------

class RAGSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    collections: Optional[list] = None


class RAGReindexRequest(BaseModel):
    source_type: Optional[str] = None


# ------------------------------------------------------------------
#  端点
# ------------------------------------------------------------------

@router.get("/stats")
def rag_stats():
    """返回 RAG 服务状态统计"""
    try:
        from backend.services.rag_knowledge_service import rag_knowledge_service
        return rag_knowledge_service.get_stats()
    except Exception as e:
        return {"ready": False, "error": str(e)}


@router.get("/health")
def rag_health():
    """RAG 健康检查"""
    try:
        from backend.services.rag_knowledge_service import rag_knowledge_service
        stats = rag_knowledge_service.get_stats()
        total_docs = sum(
            c["doc_count"] for c in stats.get("collections", {}).values()
        )
        return {
            "status": "degraded" if stats.get("degraded") else ("healthy" if stats["ready"] else "not_initialized"),
            "ready": stats["ready"],
            "degraded": stats.get("degraded", False),
            "total_documents": total_docs,
            "embedding_model": stats.get("embedding_model"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/qaa-stats")
def rag_qaa_stats():
    """QAA 实时沉淀库统计（平仓教训实时写入的那套 ChromaDB）。

    与 /stats 展示的 reindex 库（backend/data/rag_chromadb，bge-large）不同：
    本库由 paper_trading_engine 每笔平仓经 qaa_trade_memory_bridge 实时写入。
    直接只读 sqlite 统计（不初始化 embedding 管线），轻量且不抢内存。
    """
    import os
    import sqlite3
    from datetime import datetime, timezone

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _env_dir = (os.getenv("QAA_CHROMA_DIR") or "").strip()
    if _env_dir:
        # 相对路径（如 ./qaa_chromadb）相对项目根解析，与后端进程 cwd 无关
        chroma_dir = _env_dir if os.path.isabs(_env_dir) else os.path.join(root, _env_dir.lstrip(".\/").lstrip("./"))
        chroma_dir = os.path.normpath(chroma_dir)
    else:
        chroma_dir = os.path.join(root, "qaa_chromadb")
    db_path = os.path.join(chroma_dir, "chroma.sqlite3")
    out = {
        "configured": {
            "embedding_backend": os.getenv("QAA_EMBEDDING_BACKEND", "hash").strip().lower(),
            "embedding_model": os.getenv("QAA_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"),
            "knowledge_backend": os.getenv("QAA_KNOWLEDGE_BACKEND", "jsonl").strip().lower(),
            "scope": os.getenv("QAA_KNOWLEDGE_SCOPE", "alpha-arena"),
        },
        "path": db_path,
        "exists": os.path.exists(db_path),
        "file_size_mb": None,
        "last_write_at": None,
        "total_docs": 0,
        "collections": {},
        "error": None,
    }
    if not os.path.exists(db_path):
        return out
    try:
        st = os.stat(db_path)
        out["file_size_mb"] = round(st.st_size / 1048576.0, 1)
        out["last_write_at"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except Exception as e:
        out["error"] = str(e)[:200]
        return out
    try:
        con = sqlite3.connect(db_path, timeout=5)
        try:
            n = con.execute("SELECT COUNT(*) FROM embeddings").fetchone()
            out["total_docs"] = int(n[0]) if n else 0
            rows = con.execute(
                "SELECT c.name, COUNT(e.id) FROM collections c "
                "LEFT JOIN segments s ON s.collection = c.id "
                "LEFT JOIN embeddings e ON e.segment_id = s.id "
                "GROUP BY c.name ORDER BY COUNT(e.id) DESC"
            ).fetchall()
            out["collections"] = {str(name): int(cnt) for name, cnt in rows}
        finally:
            con.close()
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


@router.post("/search")
def rag_search(req: RAGSearchRequest):
    """手动测试语义检索"""
    try:
        from backend.services.rag_knowledge_service import rag_knowledge_service

        if not rag_knowledge_service.is_ready:
            raise HTTPException(
                status_code=503,
                detail="RAG 服务未初始化。请先调用 POST /api/rag/init",
            )

        results = rag_knowledge_service.retrieve(
            query_text=req.query,
            top_k=req.top_k,
            collections=req.collections,
        )

        flat_results = []
        for coll_name, items in results.items():
            for item in items:
                flat_results.append({
                    "collection": coll_name,
                    "text": item["text"][:300],
                    "distance": round(item["distance"], 4),
                    "metadata": item["metadata"],
                })

        return {
            "query": req.query,
            "total_results": len(flat_results),
            "results": flat_results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reindex")
def rag_reindex(req: RAGReindexRequest, db: Session = Depends(_get_db)):
    """
    触发索引重建。

    source_type 为空时全量重建所有 collection，
    指定值时只重建对应的 collection。
    """
    try:
        from backend.services.rag_knowledge_service import rag_knowledge_service

        if not rag_knowledge_service.is_ready:
            raise HTTPException(
                status_code=503,
                detail="RAG 服务未初始化。请先调用 POST /api/rag/init",
            )

        if req.source_type:
            count = rag_knowledge_service.index_from_db(
                db, req.source_type, incremental=False
            )
            return {"source_type": req.source_type, "indexed": count}
        else:
            counts = rag_knowledge_service.full_reindex(db)
            return {"full_reindex": True, "counts": counts}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/init")
def rag_init(force: bool = False, db: Session = Depends(_get_db)):
    """
    手动触发 RAG 首次初始化：
    1. 加载 embedding 模型（~1.5GB, 需要30-60s）
    2. 初始化 ChromaDB
    3. 执行首次全量索引

    参数:
    - force: 强制重新初始化（重置 degraded 状态）
    """
    try:
        import os
        from backend.services.rag_knowledge_service import (
            rag_knowledge_service,
            is_embedding_model_cached,
        )

        # 根据模型缓存状态决定是否使用离线模式
        if is_embedding_model_cached():
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)

        # 检查当前状态
        is_degraded = getattr(rag_knowledge_service, 'is_degraded', False)

        if rag_knowledge_service.is_ready and not is_degraded:
            stats = rag_knowledge_service.get_stats()
            total_docs = sum(
                c["doc_count"] for c in stats.get("collections", {}).values()
            )
            if total_docs > 0:
                return {
                    "status": "already_initialized",
                    "total_documents": total_docs,
                }

        # force 或 degraded 状态下重置，允许重新初始化
        if force or is_degraded:
            rag_knowledge_service.reset_for_retry()

        def _background_init():
            _db = SessionLocal()
            try:
                rag_knowledge_service._ensure_ready()
                if rag_knowledge_service.is_ready and not rag_knowledge_service.is_degraded:
                    rag_knowledge_service.full_reindex(_db)
                    logger.info("[RAG API] 后台初始化 + 全量索引完成")
                elif rag_knowledge_service.is_degraded:
                    logger.warning("[RAG API] 初始化完成但处于 degraded 模式（embedding 模型不可用）")
            except Exception as err:
                logger.error(f"[RAG API] 后台初始化异常: {err}", exc_info=True)
            finally:
                _db.close()

        threading.Thread(target=_background_init, daemon=True).start()

        return {
            "status": "initializing",
            "message": "RAG 正在后台初始化（加载模型 + 索引数据），预计1-3分钟完成。请通过 GET /api/rag/health 检查状态。",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
