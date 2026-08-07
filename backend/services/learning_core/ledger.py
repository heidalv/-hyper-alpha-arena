"""统一进化学习内核 — 血缘账本 LearningLedger

职责：
  1. 把每个 EvolutionEnvelope 持久化到独立 SQLite（data/learning_core.db），
     不改动主库表结构（与 Hermes 一致的隔离策略，降低实盘迁移风险）。
  2. 按 lineage_id 回放完整血缘链路，支持"从假设到部署"的可追溯查询。
  3. 记录后通过 ws_broadcast_hub 实时推送到前端"进化中枢"实时管线（需求 4）。

线程安全：SQLite 写入用全局锁串行化；读取各自开连接。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from .envelope import EvolutionEnvelope
from . import flags

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    # backend/services/learning_core -> 上溯 3 层到项目根
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


LEDGER_DB_PATH = os.path.join(_repo_root(), "data", "learning_core.db")

_write_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS evolution_lineage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id  TEXT NOT NULL UNIQUE,
    lineage_id   TEXT NOT NULL,
    parent_id    TEXT,
    stage        TEXT NOT NULL,
    source       TEXT NOT NULL,
    symbol       TEXT,
    status       TEXT NOT NULL,
    payload      TEXT,          -- JSON
    metrics      TEXT,          -- JSON
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lineage_id ON evolution_lineage(lineage_id);
CREATE INDEX IF NOT EXISTS idx_stage ON evolution_lineage(stage);
CREATE INDEX IF NOT EXISTS idx_created_at ON evolution_lineage(created_at);
"""


class LearningLedger:
    """血缘账本单例。"""

    def __init__(self) -> None:
        self._initialized = False

    # ── 连接 / 初始化 ──

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        os.makedirs(os.path.dirname(LEDGER_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(LEDGER_DB_PATH, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        with _write_lock:
            if self._initialized:
                return
            try:
                with self._connect() as conn:
                    conn.executescript(_DDL)
                    conn.commit()
                self._initialized = True
                logger.info("[LearningLedger] 已初始化 %s", LEDGER_DB_PATH)
            except Exception as exc:
                logger.error("[LearningLedger] 初始化失败: %s", exc)

    # ── 写入 ──

    def record(self, env: EvolutionEnvelope, *, broadcast: bool = True) -> EvolutionEnvelope:
        """持久化一条 envelope 并（可选）实时广播。返回原 envelope 以便链式调用。"""
        if not flags.get_flag("LEARNING_LEDGER_ENABLED"):
            return env
        self.ensure_initialized()

        # ── 整改#21：PBO-aware — 记录前自动累计该 lineage 的跨代 trial 数 ──
        try:
            import os as _os
            if _os.getenv("PBO_AUDIT_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                _prior = len(self.get_lineage(env.lineage_id))
                env.cumulative_trial_count = _prior + 1
                env.metrics["cumulative_trial_count"] = env.cumulative_trial_count
        except Exception:
            pass
        try:
            with _write_lock:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO evolution_lineage
                        (envelope_id, lineage_id, parent_id, stage, source, symbol,
                         status, payload, metrics, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            env.envelope_id,
                            env.lineage_id,
                            env.parent_id,
                            env.stage,
                            env.source,
                            env.symbol,
                            env.status,
                            json.dumps(env.payload, ensure_ascii=False, default=str),
                            json.dumps(env.metrics, ensure_ascii=False, default=str),
                            env.created_at,
                        ),
                    )
                    conn.commit()
        except Exception as exc:
            logger.error("[LearningLedger] 写入失败 envelope=%s: %s", env.envelope_id, exc)

        if broadcast:
            self._broadcast(env)
        return env

    def _broadcast(self, env: EvolutionEnvelope) -> None:
        try:
            from backend.services.ws_broadcast import ws_broadcast_hub
            ws_broadcast_hub.broadcast_learning_event(env.to_dict())
        except Exception as exc:  # 广播失败不影响主流程
            logger.debug("[LearningLedger] 广播失败: %s", exc)

    # ── 读取 ──

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("payload", "metrics"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else {}
            except Exception:
                d[k] = {}
        return d

    def get_lineage(self, lineage_id: str) -> List[Dict[str, Any]]:
        """按 lineage_id 返回该链路的全部 envelope（按时间升序），用于血缘回放。"""
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM evolution_lineage WHERE lineage_id = ? ORDER BY id ASC",
                    (lineage_id,),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.error("[LearningLedger] get_lineage 失败: %s", exc)
            return []

    def recent(self, limit: int = 100, stage: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回最近的 envelope（可按 stage 过滤），用于实时管线首屏。"""
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                if stage:
                    rows = conn.execute(
                        "SELECT * FROM evolution_lineage WHERE stage = ? ORDER BY id DESC LIMIT ?",
                        (stage, int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM evolution_lineage ORDER BY id DESC LIMIT ?",
                        (int(limit),),
                    ).fetchall()
                return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.error("[LearningLedger] recent 失败: %s", exc)
            return []

    def recent_lineages(self, limit: int = 30) -> List[Dict[str, Any]]:
        """返回最近的血缘链路摘要（每条链路一行：起止阶段、节点数、最新状态）。"""
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT lineage_id,
                           COUNT(*) AS node_count,
                           MIN(created_at) AS started_at,
                           MAX(created_at) AS updated_at
                    FROM evolution_lineage
                    GROUP BY lineage_id
                    ORDER BY MAX(id) DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
                out: List[Dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    last = conn.execute(
                        "SELECT stage, status, source, symbol FROM evolution_lineage "
                        "WHERE lineage_id = ? ORDER BY id DESC LIMIT 1",
                        (d["lineage_id"],),
                    ).fetchone()
                    if last:
                        d.update({
                            "latest_stage": last["stage"],
                            "latest_status": last["status"],
                            "latest_source": last["source"],
                            "symbol": last["symbol"],
                        })
                    out.append(d)
                return out
        except Exception as exc:
            logger.error("[LearningLedger] recent_lineages 失败: %s", exc)
            return []

    def stats(self) -> Dict[str, Any]:
        """账本统计：总条数、各阶段计数、各状态计数。"""
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) AS c FROM evolution_lineage").fetchone()["c"]
                lineages = conn.execute(
                    "SELECT COUNT(DISTINCT lineage_id) AS c FROM evolution_lineage"
                ).fetchone()["c"]
                by_stage = {
                    r["stage"]: r["c"]
                    for r in conn.execute(
                        "SELECT stage, COUNT(*) AS c FROM evolution_lineage GROUP BY stage"
                    ).fetchall()
                }
                by_status = {
                    r["status"]: r["c"]
                    for r in conn.execute(
                        "SELECT status, COUNT(*) AS c FROM evolution_lineage GROUP BY status"
                    ).fetchall()
                }
                return {
                    "total_envelopes": total,
                    "total_lineages": lineages,
                    "by_stage": by_stage,
                    "by_status": by_status,
                }
        except Exception as exc:
            logger.error("[LearningLedger] stats 失败: %s", exc)
            return {"total_envelopes": 0, "total_lineages": 0, "by_stage": {}, "by_status": {}}


# 单例
ledger = LearningLedger()
