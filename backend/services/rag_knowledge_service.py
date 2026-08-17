"""
RAG 知识库服务 — 向量语义检索引擎

与现有学习进化系统深度集成：
- 从 DecisionSnapshot / StrategyMemory / TradingWisdom / TradeMemoryRecord 索引
- 加载 K线形态百科 + 宏观事件案例的静态知识
- 被 ExperienceRetriever 调用，注入 MasterController prompt
- 通过 EvolutionScheduler 的定时任务链触发增量索引

技术栈：
- Embedding: BAAI/bge-large-zh-v1.5 (CPU 模式，延迟加载)
- 向量数据库: ChromaDB (本地持久化)
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Collection 名称常量
# ---------------------------------------------------------------------------
COLL_TRADE_DECISIONS = "trade_decisions"
COLL_STRATEGY_LESSONS = "strategy_lessons"
COLL_TRADING_WISDOM = "trading_wisdom"
COLL_TRADE_MEMORY = "trade_memory"
COLL_STATIC_KNOWLEDGE = "static_knowledge"

ALL_COLLECTIONS = [
    COLL_TRADE_DECISIONS,
    COLL_STRATEGY_LESSONS,
    COLL_TRADING_WISDOM,
    COLL_TRADE_MEMORY,
    COLL_STATIC_KNOWLEDGE,
]

# Embedding 模型名称（首次使用时自动下载 ~1.3GB）
EMBEDDING_MODEL_NAME = "BAAI/bge-large-zh-v1.5"

# ChromaDB 持久化目录（相对于 backend/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
CHROMADB_PERSIST_DIR = str(_BACKEND_DIR / "data" / "rag_chromadb")


def is_embedding_model_cached() -> bool:
    """检查 embedding 模型是否已缓存到本地 HuggingFace cache。
    用于决定启动时是否启用离线模式。
    """
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    model_dir = hf_home / "hub" / f"models--{EMBEDDING_MODEL_NAME.replace('/', '--')}"
    if model_dir.exists():
        # 验证 snapshots 目录下有实际模型文件
        snapshots_dir = model_dir / "snapshots"
        if snapshots_dir.exists():
            for snap in snapshots_dir.iterdir():
                if snap.is_dir() and any(snap.iterdir()):
                    return True
    return False


class RAGKnowledgeService:
    """RAG 知识库核心服务（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._chroma_client = None
        self._embed_model = None
        self._embed_lock = threading.Lock()
        # [2026-08-07 fix] 索引与检索共用同一把可重入锁：chromadb Rust 绑定
        # (chromadb_rust_bindings.pyd) 非线程安全，后端进程内 init 后台线程
        # full_reindex 与 retrieve()/get_stats() 并发访问时曾触发访问冲突
        # 导致整个 python.exe 崩溃（事件日志 Id 1000，故障模块
        # chromadb_rust_bindings.pyd）。统一串行化所有 Chroma 操作以杜绝并发。
        self._chroma_ops_lock = threading.RLock()
        self._index_lock = self._chroma_ops_lock  # 索引与检索共用同一把锁
        # [2026-08-07 fix] 同源索引互斥：多个调度任务（daily_wisdom_refresh /
        # daily_signal_weight_update / weekly_rag_full_reindex）会并发触发同一
        # 数据源的 index_from_db。若每个任务都重新 embedding（分钟级），RAG 读
        # 路径会被锁阻塞数十分钟。busy 标记使同源并发触发直接跳过。
        self._index_busy: Dict[str, bool] = {c: False for c in ALL_COLLECTIONS}
        self._index_busy_lock = threading.Lock()
        self._ready = False
        self._degraded = False          # degraded 模式：embedding 不可用，查询返回空
        self._init_permanently_failed = False  # 永久失败标记，避免反复重试

        # [2026-07-11 修复] 原 retrieve() 在 degraded 模式下静默返回 {}，调用方
        # （trade_memory_context 等）拿到空结果后无法区分"真的没有相关记忆"还是
        # "记忆库其实是空转的（embedding 模型没加载起来）"，等于"记忆库其实是空的
        # 但没人发现"。这里加计数器+限频告警，让这种情况能被观测到（/api/health 或
        # 日志巡检能看到 degraded_query_count 持续增长）。
        self._degraded_query_count = 0
        self._last_degraded_warn_ts = 0.0

        # 索引统计
        self._last_index_time: Dict[str, Optional[datetime]] = {
            c: None for c in ALL_COLLECTIONS
        }
        self._doc_counts: Dict[str, int] = {c: 0 for c in ALL_COLLECTIONS}

        logger.info("[RAG] 知识库服务初始化（延迟加载模式）")

    # ------------------------------------------------------------------
    #  延迟初始化：首次使用时才加载 embedding 模型和 ChromaDB
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> bool:
        """确保 ChromaDB + Embedding 模型已加载。返回 True 表示就绪。"""
        if self._ready:
            return True
        # 永久失败快速路径（仅 ImportError：依赖包未安装）
        if self._init_permanently_failed:
            return self._ready

        with self._embed_lock:
            if self._ready:
                return True
            try:
                self._init_chromadb()
                self._init_embedding()
                # embedding 模型加载失败但 ChromaDB 可用时进入 degraded 模式
                # 注意：不设 _init_permanently_failed，允许后续通过 reset_for_retry() 重试
                if self._embed_model is None:
                    self._ready = True
                    self._degraded = True
                    logger.warning("[RAG] Embedding 模型不可用，进入 degraded 模式（查询返回空，可通过 reset_for_retry 重试）")
                    return True
                self._ready = True
                logger.info("[RAG] ChromaDB + Embedding 模型加载完成，服务就绪")
                return True
            except ImportError as e:
                # 依赖包未安装是真正的不可恢复错误
                logger.warning(f"[RAG] 依赖包缺失（不可恢复）: {e}")
                self._init_permanently_failed = True
                return False
            except Exception as e:
                # 网络/IO 等临时错误，允许后续重试
                logger.warning(f"[RAG] 初始化失败（可重试）: {e}")
                return False

    def reset_for_retry(self) -> None:
        """重置 degraded 状态，允许重新尝试加载 embedding 模型。
        用于 API 端点强制重试或网络恢复后手动触发。
        """
        with self._embed_lock:
            if self._degraded and self._embed_model is None:
                self._ready = False
                self._degraded = False
                self._init_permanently_failed = False
                logger.info("[RAG] 状态已重置，允许重新初始化 embedding 模型")

    def _init_chromadb(self):
        """初始化 ChromaDB 持久化客户端"""
        import chromadb

        os.makedirs(CHROMADB_PERSIST_DIR, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(
            path=CHROMADB_PERSIST_DIR,
        )

        for name in ALL_COLLECTIONS:
            self._chroma_client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        logger.info(f"[RAG] ChromaDB 初始化完成，持久化目录: {CHROMADB_PERSIST_DIR}")

    def _init_embedding(self):
        """加载 bge-large-zh-v1.5 embedding 模型（CPU 模式，强制离线）"""
        # 强制离线：模型已缓存在本地 HF cache，不需要联网检查。
        # 之前的 setdefault 会被其他代码 pop 掉，导致联网超时 60 秒。
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"

        from sentence_transformers import SentenceTransformer

        # 注意：不在此处调整 torch 线程数。run_uvicorn_dev.py 启动时已锁定
        # OMP_NUM_THREADS=1 + torch.set_num_threads(1)（防 OpenMP 线程池无限
        # 增长 + 降低 cu124 CPU 推理段错误概率），此处覆盖会破坏该保护。
        t0 = time.time()
        try:
            # [2026-08-16 GPU 切换] 旧实现强制 CPU，bge-large 推理吃满全部核心。
            # 本机有 CUDA 时切 GPU（8G 显存足够 bge-large fp32），不可用时回退 CPU。
            _device = "cpu"
            try:
                import torch as _torch
                if _torch.cuda.is_available():
                    _device = "cuda"
            except Exception:
                _device = "cpu"
            self._embed_model = SentenceTransformer(
                EMBEDDING_MODEL_NAME,
                device=_device,
            )
            logger.info(f"[RAG] Embedding 模型加载完成: {EMBEDDING_MODEL_NAME} device={_device} ({time.time()-t0:.1f}s)")
        except Exception as load_err:
            logger.warning(
                f"[RAG] Embedding 加载失败（离线模式）: {load_err}"
            )
            # 不再尝试联网下载（本地缓存已有模型，联网只会超时 60 秒）
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            self._embed_model = None
            logger.warning("[RAG] Embedding 模型加载失败，进入 degraded 模式")

    # ------------------------------------------------------------------
    #  Embedding 计算
    # ------------------------------------------------------------------

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量计算文本 embedding"""
        if not texts:
            return []
        if self._embed_model is None:
            return []
        embeddings = self._embed_model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=64,
        )
        return embeddings.tolist()

    # ------------------------------------------------------------------
    #  索引接口（增量 + 全量）
    # ------------------------------------------------------------------

    def index_from_db(
        self,
        db,
        source_type: str,
        incremental: bool = True,
        days: int = 7,
    ) -> int:
        """
        从数据库索引到 ChromaDB。

        source_type: COLL_TRADE_DECISIONS / COLL_STRATEGY_LESSONS /
                     COLL_TRADING_WISDOM / COLL_TRADE_MEMORY
        incremental: True 只索引 days 天内的新数据
        返回本次索引的文档数量。
        """
        if not self._ensure_ready():
            logger.warning(f"[RAG] 服务未就绪，跳过索引 {source_type}")
            return 0
        if self._degraded:
            logger.debug(f"[RAG] degraded 模式，跳过索引 {source_type}")
            return 0

        # 同源互斥：已有同源索引在执行则跳过（防多调度任务排队重复 embedding）
        with self._index_busy_lock:
            if self._index_busy.get(source_type):
                logger.info(f"[RAG] 索引 {source_type} 已在执行中，跳过本次触发")
                return 0
            self._index_busy[source_type] = True

        try:
            # [锁外] DB 查询 + 文档构建 + embedding（分钟级纯 CPU 计算，
            # 不持有 Chroma 锁——2026-08-07 曾因 embedding 在锁内导致
            # 全部 RAG 读路径被冻结数分钟，health 端点 30s 超时）
            payload = self._build_index_payload(db, source_type, incremental, days)
            if not payload or not payload[0]:
                return 0
            ids, docs, metas, embeddings = payload

            # [锁内] 仅 Chroma 写操作（秒级），防 Rust 绑定并发崩溃
            with self._index_lock:
                # RAG 索引是全局知识库操作，必须穿透 RLS：scheduler/后台线程
                # 无租户身份时 RLS fail-closed 会隐藏数据（曾致 0 条假象）
                from backend.core.tenant import system_identity
                with system_identity():
                    count = self._write_collection(
                        source_type, ids, docs, embeddings, metas
                    )
                self._last_index_time[source_type] = datetime.now(timezone.utc)
                self._doc_counts[source_type] = count
                logger.info(f"[RAG] 索引 {source_type} 完成: {count} 条文档")
                return count
        except Exception as e:
            logger.error(f"[RAG] 索引 {source_type} 异常: {e}", exc_info=True)
            # [2026-08-06 fix] 索引异常后必须回滚，否则事务进入 failed 状态，
            # 后续所有源在同一 session 上全部 InFailedSqlTransaction 级联失败。
            try:
                db.rollback()
            except Exception:
                pass
            return 0
        finally:
            with self._index_busy_lock:
                self._index_busy[source_type] = False

    def _build_index_payload(
        self, db, source_type: str, incremental: bool, days: int
    ):
        """[锁外] 查询 DB + 构建文档 + embedding。

        返回 (ids, docs, metas, embeddings)；无数据返回 None。
        """
        if source_type == COLL_TRADE_DECISIONS:
            return self._build_trade_decisions(db, incremental, days)
        if source_type == COLL_STRATEGY_LESSONS:
            return self._build_strategy_lessons(db)
        if source_type == COLL_TRADING_WISDOM:
            return self._build_trading_wisdom(db)
        if source_type == COLL_TRADE_MEMORY:
            return self._build_trade_memory(db, days)
        logger.warning(f"[RAG] 未知源类型: {source_type}")
        return None

    def _write_collection(
        self,
        source_type: str,
        ids: List[str],
        docs: List[str],
        embeddings: List[List[float]],
        metas: List[Dict],
        replace_all: bool = False,
    ) -> int:
        """[锁内] 写 Chroma。

        trade_decisions 用 upsert 分批（保留历史）；其余源全量替换
        （先清空再写入，与各源语义一致）。
        """
        coll = self._chroma_client.get_or_create_collection(
            name=source_type,
            metadata={"hnsw:space": "cosine"},
        )
        if replace_all or source_type != COLL_TRADE_DECISIONS:
            existing = coll.count()
            if existing > 0:
                old_ids = coll.get()["ids"]
                if old_ids:
                    coll.delete(ids=old_ids)
            coll.add(
                ids=ids, documents=docs, embeddings=embeddings, metadatas=metas
            )
        else:
            batch_size = 100
            for i in range(0, len(ids), batch_size):
                coll.upsert(
                    ids=ids[i:i + batch_size],
                    documents=docs[i:i + batch_size],
                    embeddings=embeddings[i:i + batch_size],
                    metadatas=metas[i:i + batch_size],
                )
        return len(ids)

    def index_static_knowledge(self) -> int:
        """索引静态知识库（K线形态 + 宏观事件）"""
        if not self._ensure_ready():
            return 0
        if self._degraded:
            return 0

        with self._index_busy_lock:
            if self._index_busy.get(COLL_STATIC_KNOWLEDGE):
                return 0
            self._index_busy[COLL_STATIC_KNOWLEDGE] = True

        try:
            # [锁外] 读 json + 构建文档 + embedding（不持有 Chroma 锁）
            ids, docs, metas = [], [], []
            data_dir = _BACKEND_DIR / "data"

            kline_file = data_dir / "kline_patterns.json"
            if kline_file.exists():
                patterns = json.loads(kline_file.read_text(encoding="utf-8"))
                for p in patterns:
                    doc_id = f"kline_{p.get('id', p.get('pattern', ''))}"
                    text = (
                        f"K线形态: {p['pattern']} | "
                        f"类型: {p.get('type', '')} | "
                        f"描述: {p.get('description', '')} | "
                        f"可靠性: {p.get('reliability', '')} | "
                        f"后续走势: {p.get('expectation', '')}"
                    )
                    ids.append(doc_id)
                    docs.append(text)
                    metas.append({
                        "source": "kline_pattern",
                        "type": p.get("type", "neutral"),
                        "reliability": p.get("reliability", "medium"),
                    })

            macro_file = data_dir / "macro_events.json"
            if macro_file.exists():
                events = json.loads(macro_file.read_text(encoding="utf-8"))
                for e in events:
                    doc_id = f"macro_{e.get('id', e.get('event', ''))}"
                    text = (
                        f"宏观事件: {e['event']} | "
                        f"日期: {e.get('date', '')} | "
                        f"影响: {e.get('impact', '')} | "
                        f"BTC变动: {e.get('btc_change', '')} | "
                        f"教训: {e.get('lesson', '')}"
                    )
                    ids.append(doc_id)
                    docs.append(text)
                    metas.append({
                        "source": "macro_event",
                        "category": e.get("category", "other"),
                        "severity": e.get("severity", "medium"),
                    })

            if not docs:
                return 0
            embeddings = self._embed_texts(docs)

            # [锁内] 清空 + 全量写入（秒级）
            with self._index_lock:
                count = self._write_collection(
                    COLL_STATIC_KNOWLEDGE, ids, docs, embeddings, metas,
                    replace_all=True,
                )
                self._last_index_time[COLL_STATIC_KNOWLEDGE] = datetime.now(timezone.utc)
                self._doc_counts[COLL_STATIC_KNOWLEDGE] = count
            logger.info(f"[RAG] 静态知识库索引完成: {count} 条")
            return count
        except Exception as e:
            logger.error(f"[RAG] 静态知识库索引异常: {e}", exc_info=True)
            return 0
        finally:
            with self._index_busy_lock:
                self._index_busy[COLL_STATIC_KNOWLEDGE] = False

    # ------------------------------------------------------------------
    #  各数据源的索引实现
    # ------------------------------------------------------------------

    def _build_trade_decisions(self, db, incremental: bool, days: int):
        """[锁外] 构建 trade_decisions 的 (ids, docs, metas, embeddings)。

        [2026-08-06 fix] DecisionSnapshot 映射到 AnalyticsBase（alpha_analytics 库），
        传入的 db 是 alpha_arena 主库 session——用主库 session 查询会命中主库里的
        旧结构同名空表（缺 session_id 等列）抛 UndefinedColumn，且异常后主库事务
        进入 failed 状态级联毒化后续索引。这里改用 AnalyticsSessionLocal 独立查询。
        """
        from backend.database.models import DecisionSnapshot
        from backend.database.connection import AnalyticsSessionLocal

        analytics_db = AnalyticsSessionLocal()
        try:
            query = analytics_db.query(DecisionSnapshot).filter(
                DecisionSnapshot.pnl.isnot(None),
            )
            if incremental:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                query = query.filter(DecisionSnapshot.timestamp >= cutoff)

            snapshots = query.order_by(DecisionSnapshot.timestamp.desc()).limit(5000).all()
        finally:
            analytics_db.close()
        if not snapshots:
            return None

        ids, docs, metas = [], [], []
        for s in snapshots:
            doc_id = f"decision_{s.id}"
            pnl = s.pnl or 0
            pnl_pct = (s.pnl_pct or 0) * 100
            regime = s.regime_at_decision or "unknown"
            reasoning = (s.ai_reasoning or "")[:300]
            lesson = s.lesson_extracted or ""
            result_label = "盈利" if pnl > 0 else "亏损"

            text = (
                f"{s.symbol} {regime} 方向:{s.direction or '?'} "
                f"置信:{s.confidence or 0:.0f}% | "
                f"推理:{reasoning} | "
                f"结果:{result_label}{pnl_pct:+.1f}% | "
                f"教训:{lesson}"
            )

            ids.append(doc_id)
            docs.append(text)
            metas.append({
                "symbol": s.symbol or "",
                "regime": regime,
                "direction": s.direction or "",
                "pnl_pct": round(pnl_pct, 2),
                "profitable": pnl > 0,
                "confidence": s.confidence or 0,
                "quality": s.quality_label or "",
                "timestamp": s.timestamp.isoformat() if s.timestamp else "",
            })

        embeddings = self._embed_texts(docs)
        return ids, docs, metas, embeddings

    def _build_strategy_lessons(self, db):
        """[锁外] 构建 strategy_lessons 的 (ids, docs, metas, embeddings)。"""
        from backend.database.models import StrategyMemory

        memories = db.query(StrategyMemory).filter(
            StrategyMemory.key_lessons.isnot(None),
        ).all()
        # [2026-08-07 fix] 查询完成后立即结束事务：后续文本构建 + embedding
        # 耗时可达 100s+，期间事务 idle-in-transaction 会被 DB LeakGuard
        # (120s 强制终止) 杀掉连接，导致下一源查询复用死连接报
        # "server closed the connection unexpectedly"（曾致 trading_wisdom=0）。
        # 注意：rollback() 会 expire 全部 ORM 对象，属性访问将触发 N+1 懒加载
        # 且每次懒加载后事务重新挂起——必须先提取标量再 rollback。
        rows = [
            (
                m.strategy_id, m.key_lessons, m.win_rate,
                m.sharpe_ratio, m.max_drawdown,
            )
            for m in memories
        ]
        db.rollback()

        if not rows:
            return None

        ids, docs, metas = [], [], []
        for strategy_id, raw_lessons, win_rate, sharpe, mdd in rows:
            lessons = raw_lessons
            if isinstance(lessons, str):
                try:
                    lessons = json.loads(lessons)
                except Exception:
                    lessons = [{"text": lessons}]
            if not isinstance(lessons, list):
                continue

            for idx, lesson in enumerate(lessons):
                if isinstance(lesson, dict):
                    text_content = lesson.get("message", lesson.get("text", str(lesson)))
                    severity = lesson.get("severity", "info")
                    lesson_type = lesson.get("type", "general")
                else:
                    text_content = str(lesson)
                    severity = "info"
                    lesson_type = "general"

                doc_id = f"lesson_{strategy_id}_{idx}"
                text = (
                    f"策略教训 [{lesson_type}]: {text_content} | "
                    f"策略胜率:{win_rate:.1%} "
                    f"夏普:{sharpe:.2f} "
                    f"最大回撤:{mdd:.1%}"
                )

                ids.append(doc_id)
                docs.append(text)
                metas.append({
                    "strategy_id": strategy_id or "",
                    "win_rate": win_rate or 0,
                    "severity": severity,
                    "lesson_type": lesson_type,
                })

        if not ids:
            return None

        embeddings = self._embed_texts(docs)
        return ids, docs, metas, embeddings

    def _index_unified_knowledge(self, db, knowledge_items: list = None) -> int:
        """Phase 2 整合: 从统一知识池增量索引到 RAG ChromaDB。

        若提供 knowledge_items，仅索引这些新条目（增量）；
        否则从 _global_ StrategyMemory 全量重建。
        [2026-08-07 fix] 外部直接调用，未经过 index_from_db 的锁包裹：
        embedding 在锁外计算，Chroma 写操作持 _chroma_ops_lock 防 Rust
        绑定并发崩溃。
        """
        from backend.database.models import StrategyMemory

        if knowledge_items:
            # 增量模式
            items = knowledge_items
        else:
            # 全量模式: 从 _global_ StrategyMemory 读取
            mem = db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == "_global_",
            ).first()
            if not mem or not mem.key_lessons:
                return 0
            items = mem.key_lessons if isinstance(mem.key_lessons, list) else []

        if not items:
            return 0

        ids, docs, metas = [], [], []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            source = item.get("source", "unknown")
            category = item.get("category", item.get("type", "general"))
            severity = item.get("severity", "info")
            title = item.get("title", item.get("lesson", ""))[:200]
            doc_id = f"unified_{source}_{idx}"
            text = (
                f"[{source}/{category}] {title} | "
                f"severity={severity}"
            )
            ids.append(doc_id)
            docs.append(text)
            metas.append({
                "source": source,
                "category": category,
                "severity": severity,
                "strategy_id": item.get("strategy_id", "_global_"),
            })

        if not ids:
            return 0

        # [锁外] embedding
        embeddings = self._embed_texts(docs)
        # [锁内] 增量添加（已有同 ID 的不会覆盖）
        with self._chroma_ops_lock:
            coll = self._chroma_client.get_or_create_collection(
                name=COLL_STRATEGY_LESSONS,
                metadata={"hnsw:space": "cosine"},
            )
            coll.add(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
        logger.info("[RAG] 统一知识索引: %d 条 (source 分布: %s)",
                     len(ids),
                     ",".join(f"{s}={sum(1 for m in metas if m['source']==s)}"
                              for s in sorted(set(m["source"] for m in metas))))
        return len(ids)

    def _build_trading_wisdom(self, db):
        """[锁外] 构建 trading_wisdom 的 (ids, docs, metas, embeddings)。

        按验证强度排序取 top N：验证强度 = effectiveness ×
        min(1, quality_hit_count/min_q) × log(1+applied)。有质量验证数据的
        智慧优先进入知识索引；全系统无验证数据时（新上线）回退全量索引。
        """
        from backend.database.models import TradingWisdom
        from backend.services.wisdom_tracker import wisdom_tracker

        wisdoms = db.query(TradingWisdom).filter(
            TradingWisdom.is_active == True,
        ).all()

        # 验证强度排序：有质量验证数据的智慧优先进入索引池
        ranked = wisdom_tracker.get_ranked_wisdom(db, limit=50)
        # [2026-08-07 fix] 查询完成后立即结束事务：长 embedding（100s+）期间
        # idle-in-transaction 连接会被 DB LeakGuard 120s 强制终止，复用死连接
        # 报 "server closed the connection unexpectedly"（曾致 trading_wisdom=0）。
        # rollback() 会 expire ORM 对象，必须先提取标量（N+1 懒加载会重新挂起事务）。
        rows = [
            (
                w.id, w.content, w.prompt_fragment, w.wisdom_type,
                w.effectiveness_score, w.sample_count, w.confidence, w.template_id,
            )
            for w in wisdoms
        ]
        db.rollback()
        ranked_map: Dict[str, float] = {}
        if ranked and any(r["strength"] > 0 for r in ranked):
            ranked_map = {r["id"]: r["strength"] for r in ranked}
            rows = [r for r in rows if r[0] in ranked_map]
            rows.sort(key=lambda r: ranked_map[r[0]], reverse=True)

        ids, docs, metas = [], [], []
        for wid, content, prompt_frag, wisdom_type, eff_score, sample_count, confidence, template_id in rows:
            doc_id = f"wisdom_{wid}"
            if isinstance(content, dict):
                content_text = json.dumps(content, ensure_ascii=False)[:400]
            else:
                content_text = str(content)[:400]

            prompt_frag = (prompt_frag or "")[:200]
            strength = ranked_map.get(wid, 0.0)
            text = (
                f"交易智慧 [{wisdom_type}]: {content_text} | "
                f"提示词片段: {prompt_frag} | "
                f"有效性:{eff_score or 0:.2f} "
                f"样本数:{sample_count or 0} "
                f"验证强度:{strength:.3f}"
            )

            ids.append(doc_id)
            docs.append(text)
            metas.append({
                "template_id": template_id or "",
                "wisdom_type": wisdom_type or "",
                "confidence": confidence or 0,
                "effectiveness": eff_score or 0,
                "strength": round(strength, 4),
            })

        if not ids:
            return None

        embeddings = self._embed_texts(docs)
        return ids, docs, metas, embeddings

    def _build_trade_memory(self, db, days: int = 30):
        """
        [锁外] 构建 trade_memory 的 (ids, docs, metas, embeddings)。
        从 TradeMemoryRecord 按 symbol+regime 聚合索引：不逐笔索引，
        而是聚合统计后生成摘要文档。
        """
        from backend.database.models import TradeMemoryRecord

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        records = db.query(TradeMemoryRecord).filter(
            TradeMemoryRecord.opened_at >= cutoff if hasattr(TradeMemoryRecord, 'opened_at')
            else True,
        ).all()
        # [2026-08-07 fix] 查询完成后立即结束事务，避免长 embedding 期间
        # idle-in-transaction 连接被 DB LeakGuard 强制终止（同 strategy_lessons）。
        # rollback() 会 expire ORM 对象，先提取标量（N+1 懒加载会重新挂起事务）。
        rows = [
            (
                r.symbol, r.market_regime, r.pnl, r.pnl_pct,
                r.leverage, r.hold_seconds, r.close_reason,
            )
            for r in records
        ]
        db.rollback()

        if not rows:
            return None

        # 按 symbol + regime 分组聚合（保留完整 symbol，避免下划线截断）
        groups: Dict[str, List] = {}
        for symbol, market_regime, pnl, pnl_pct, leverage, hold_seconds, close_reason in rows:
            key = f"{symbol}_{market_regime or 'unknown'}"
            groups.setdefault(key, []).append(
                (symbol, market_regime or "unknown", pnl, pnl_pct, leverage, hold_seconds, close_reason)
            )

        ids, docs, metas = [], [], []
        for key, recs in groups.items():
            symbol = recs[0][0]
            regime = recs[0][1]
            total = len(recs)
            wins = [r for r in recs if (r[2] or 0) > 0]
            losses = [r for r in recs if (r[2] or 0) <= 0]
            win_rate = len(wins) / total if total > 0 else 0
            avg_win = (sum(r[3] or 0 for r in wins) / max(len(wins), 1)) * 100
            avg_loss = (sum(r[3] or 0 for r in losses) / max(len(losses), 1)) * 100
            avg_leverage = sum(r[4] or 0 for r in recs) / total
            avg_hold = sum(r[5] or 0 for r in recs) / total

            top_reason = {}
            for r in losses:
                reason = r[6] or "unknown"
                top_reason[reason] = top_reason.get(reason, 0) + 1
            worst_reason = max(top_reason, key=top_reason.get) if top_reason else "none"

            doc_id = f"memory_{key}"
            text = (
                f"{symbol} 在 {regime} 环境下最近{total}笔交易: "
                f"胜率{win_rate:.0%} 均盈{avg_win:+.1f}% 均亏{avg_loss:+.1f}% | "
                f"平均杠杆{avg_leverage:.0f}x 平均持仓{avg_hold/3600:.1f}h | "
                f"主要亏损原因:{worst_reason}"
            )

            ids.append(doc_id)
            docs.append(text)
            metas.append({
                "symbol": symbol,
                "regime": regime,
                "total_trades": total,
                "win_rate": round(win_rate, 3),
                "avg_win_pct": round(avg_win, 2),
                "avg_loss_pct": round(avg_loss, 2),
            })

        if not ids:
            return None

        embeddings = self._embed_texts(docs)
        return ids, docs, metas, embeddings

    # ------------------------------------------------------------------
    #  语义检索
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query_text: str,
        top_k: int = 5,
        collections: Optional[List[str]] = None,
        metadata_filter: Optional[Dict] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        跨 collection 语义检索。

        返回: { collection_name: [ {text, metadata, distance}, ... ] }
        """
        if not self._ensure_ready():
            return {}
        if self._degraded:
            self._degraded_query_count += 1
            # 限频告警（60s 一次），避免高频调用路径把日志刷爆，但确保问题不会被淹没。
            _now = time.time()
            if _now - self._last_degraded_warn_ts > 60:
                self._last_degraded_warn_ts = _now
                logger.warning(
                    f"[RAG] degraded 模式下 retrieve('{query_text[:30]}...') 直接返回空结果 "
                    f"(累计 {self._degraded_query_count} 次) —— embedding 模型未加载成功，"
                    f"记忆库实际未被检索，可调用 reset_for_retry() 重试"
                )
            return {}

        target_collections = collections or ALL_COLLECTIONS
        query_embedding = self._embed_texts([query_text])[0]

        results: Dict[str, List[Dict]] = {}
        # [2026-08-07 fix] 检索访问 Chroma 必须持有 _chroma_ops_lock（与索引
        # 互斥），否则与 full_reindex 后台线程并发时 Rust 绑定崩溃（进程级）。
        # 锁获取带超时：索引写操作（chroma 持久化慢，单次 10-40s）期间
        # 快速失败返回空，绝不长时间占用调用线程/AnyIO worker（曾因 3s 等待
        # 批量挤占 worker 池导致 health 等无关 API 排队超时）。
        if not self._chroma_ops_lock.acquire(timeout=0.3):
            self._degraded_query_count += 1
            logger.warning(
                f"[RAG] 检索锁获取超时（Chroma 操作进行中），"
                f"retrieve('{query_text[:30]}...') 返回空结果 "
                f"(累计 {self._degraded_query_count} 次)"
            )
            return {}
        try:
            for coll_name in target_collections:
                try:
                    coll = self._chroma_client.get_collection(coll_name)
                    if coll.count() == 0:
                        continue

                    query_params = {
                        "query_embeddings": [query_embedding],
                        "n_results": min(top_k, coll.count()),
                    }
                    if metadata_filter:
                        query_params["where"] = metadata_filter

                    raw = coll.query(**query_params)
                    items = []
                    if raw and raw.get("documents") and raw["documents"][0]:
                        for i, doc in enumerate(raw["documents"][0]):
                            item = {
                                "text": doc,
                                "distance": raw["distances"][0][i] if raw.get("distances") else 0,
                                "metadata": raw["metadatas"][0][i] if raw.get("metadatas") else {},
                            }
                            items.append(item)

                    if items:
                        results[coll_name] = items
                except Exception as e:
                    logger.debug(f"[RAG] 检索 {coll_name} 异常: {e}")
        finally:
            self._chroma_ops_lock.release()

        return results

    # ------------------------------------------------------------------
    #  全量重建
    # ------------------------------------------------------------------

    def full_reindex(self, db) -> Dict[str, int]:
        """全量重建所有 collection"""
        counts = {}
        counts[COLL_TRADE_DECISIONS] = self.index_from_db(
            db, COLL_TRADE_DECISIONS, incremental=False
        )
        counts[COLL_STRATEGY_LESSONS] = self.index_from_db(
            db, COLL_STRATEGY_LESSONS
        )
        counts[COLL_TRADING_WISDOM] = self.index_from_db(
            db, COLL_TRADING_WISDOM
        )
        counts[COLL_TRADE_MEMORY] = self.index_from_db(
            db, COLL_TRADE_MEMORY
        )
        counts[COLL_STATIC_KNOWLEDGE] = self.index_static_knowledge()
        logger.info(f"[RAG] 全量重建完成: {counts}")
        return counts

    # ------------------------------------------------------------------
    #  统计 / 健康检查
    # ------------------------------------------------------------------

    def _update_doc_count(self, coll_name: str):
        """更新某个 collection 的文档计数

        [2026-08-07 fix] 锁获取 timeout=0.2s：索引（embedding 已移出锁外，
        锁内仅秒级 Chroma 写）进行期间短暂等待；锁不可得时保留缓存计数，
        health/get_stats 端点永不阻塞（曾因索引持锁导致 health 30s 超时）。
        """
        try:
            if self._chroma_client and self._chroma_ops_lock.acquire(timeout=0.2):
                try:
                    coll = self._chroma_client.get_collection(coll_name)
                    self._doc_counts[coll_name] = coll.count()
                finally:
                    self._chroma_ops_lock.release()
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """返回 RAG 服务状态统计"""
        if self._chroma_client:
            for name in ALL_COLLECTIONS:
                self._update_doc_count(name)

        return {
            "ready": self._ready,
            "degraded": self._degraded,
            "degraded_query_count": self._degraded_query_count,
            "embedding_model": EMBEDDING_MODEL_NAME if self._embed_model else None,
            "persist_dir": CHROMADB_PERSIST_DIR,
            "collections": {
                name: {
                    "doc_count": self._doc_counts.get(name, 0),
                    "last_indexed": (
                        self._last_index_time[name].isoformat()
                        if self._last_index_time.get(name)
                        else None
                    ),
                }
                for name in ALL_COLLECTIONS
            },
        }

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def is_degraded(self) -> bool:
        """是否处于 degraded 模式（ChromaDB 可用但 embedding 模型不可用）。"""
        return self._degraded


# ---------------------------------------------------------------------------
#  单例导出
# ---------------------------------------------------------------------------
rag_knowledge_service = RAGKnowledgeService()
