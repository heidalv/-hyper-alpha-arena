"""RL 经验回放缓冲区 ReplayBuffer

统一存储来自"回测回放 + 在线交易 outcome"的转移样本 (state, action, reward, next_state, done)，
作为 RL 决策 agent（P4）离线/在线训练的数据源，实现"回测→优化→RL"闭环（方案需求 3/6）。

独立 SQLite（data/rl_replay.db），不侵入主库；写入串行化。
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


REPLAY_DB_PATH = os.path.join(_repo_root(), "data", "rl_replay.db")

_write_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS rl_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT,
    source      TEXT NOT NULL,       -- backtest | live | paper | synthetic
    state       TEXT NOT NULL,       -- JSON: 特征向量/观测
    action      INTEGER NOT NULL,    -- 0 hold | 1 open_long | 2 open_short | 3 close
    reward      REAL NOT NULL,
    next_state  TEXT,                -- JSON
    done        INTEGER NOT NULL DEFAULT 0,
    lineage_id  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rl_source ON rl_transitions(source);
CREATE INDEX IF NOT EXISTS idx_rl_symbol ON rl_transitions(symbol);
"""


class ReplayBuffer:
    """经验回放缓冲区（单例 replay_buffer）。"""

    def __init__(self) -> None:
        self._initialized = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        os.makedirs(os.path.dirname(REPLAY_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(REPLAY_DB_PATH, timeout=15.0)
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
                logger.info("[ReplayBuffer] 已初始化 %s", REPLAY_DB_PATH)
            except Exception as exc:
                logger.error("[ReplayBuffer] 初始化失败: %s", exc)

    # ── 写入 ──

    def add_transition(
        self,
        *,
        state: List[float] | Dict[str, Any],
        action: int,
        reward: float,
        next_state: Optional[List[float] | Dict[str, Any]] = None,
        done: bool = False,
        symbol: Optional[str] = None,
        source: str = "backtest",
        lineage_id: Optional[str] = None,
    ) -> None:
        self.ensure_initialized()
        try:
            with _write_lock:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO rl_transitions
                        (symbol, source, state, action, reward, next_state, done, lineage_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            source,
                            json.dumps(state, default=str),
                            int(action),
                            float(reward),
                            json.dumps(next_state, default=str) if next_state is not None else None,
                            1 if done else 0,
                            lineage_id,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    conn.commit()
        except Exception as exc:
            logger.debug("[ReplayBuffer] add_transition 失败: %s", exc)

    def add_batch(self, transitions: List[Dict[str, Any]]) -> int:
        """批量写入转移（回测回放一次性灌入）。返回写入条数。"""
        self.ensure_initialized()
        n = 0
        try:
            with _write_lock:
                with self._connect() as conn:
                    for t in transitions:
                        conn.execute(
                            """
                            INSERT INTO rl_transitions
                            (symbol, source, state, action, reward, next_state, done, lineage_id, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                t.get("symbol"),
                                t.get("source", "backtest"),
                                json.dumps(t.get("state"), default=str),
                                int(t.get("action", 0)),
                                float(t.get("reward", 0.0)),
                                json.dumps(t.get("next_state"), default=str) if t.get("next_state") is not None else None,
                                1 if t.get("done") else 0,
                                t.get("lineage_id"),
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )
                        n += 1
                    conn.commit()
        except Exception as exc:
            logger.debug("[ReplayBuffer] add_batch 失败: %s", exc)
        return n

    # ── 读取 / 采样 ──

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in ("state", "next_state"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else None
            except Exception:
                d[k] = None
        d["done"] = bool(d.get("done"))
        return d

    def sample(self, batch_size: int = 64, source: Optional[str] = None) -> List[Dict[str, Any]]:
        """随机采样一批转移用于训练。"""
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                if source:
                    rows = conn.execute(
                        "SELECT * FROM rl_transitions WHERE source = ? ORDER BY RANDOM() LIMIT ?",
                        (source, int(batch_size)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM rl_transitions ORDER BY RANDOM() LIMIT ?",
                        (int(batch_size),),
                    ).fetchall()
                return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.debug("[ReplayBuffer] sample 失败: %s", exc)
            return []

    def count(self) -> int:
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                return conn.execute("SELECT COUNT(*) AS c FROM rl_transitions").fetchone()["c"]
        except Exception:
            return 0

    def stats(self) -> Dict[str, Any]:
        self.ensure_initialized()
        try:
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) AS c FROM rl_transitions").fetchone()["c"]
                by_source = {
                    r["source"]: r["c"]
                    for r in conn.execute(
                        "SELECT source, COUNT(*) AS c FROM rl_transitions GROUP BY source"
                    ).fetchall()
                }
                by_action = {
                    r["action"]: r["c"]
                    for r in conn.execute(
                        "SELECT action, COUNT(*) AS c FROM rl_transitions GROUP BY action"
                    ).fetchall()
                }
                avg_reward = conn.execute(
                    "SELECT AVG(reward) AS a FROM rl_transitions"
                ).fetchone()["a"]
                return {
                    "total": total,
                    "by_source": by_source,
                    "by_action": by_action,
                    "avg_reward": round(float(avg_reward), 6) if avg_reward is not None else 0.0,
                }
        except Exception as exc:
            logger.debug("[ReplayBuffer] stats 失败: %s", exc)
            return {"total": 0, "by_source": {}, "by_action": {}, "avg_reward": 0.0}


# 单例
replay_buffer = ReplayBuffer()
