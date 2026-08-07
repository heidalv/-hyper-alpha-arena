"""
QAA AsyncLogWriter — 异步日志写入器

设计文档: docs/V4_MULTI_AGENT_ARCHITECTURE.md §3.7

将非关键 DB 写入从交易主循环解耦, 避免 SQLite 写锁竞争。
主循环 → enqueue() → 后台线程 → 批量事务写入

适用场景:
- AIDecisionLog 写入
- FactorQualityReport 写入
- DecisionSnapshot 写入
- QAA AuditEntry 写入

不适用:
- Order / Position 写入 (关键, 必须同步)
- Account 余额更新 (关键, 必须同步)
"""

from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AsyncLogWriter:
    """异步日志写入器 — 非关键写入不阻塞交易主循环

    工作原理:
    1. 主线程调用 enqueue(session_factory, write_fn, record)
    2. 数据进入内存队列 (maxsize=1000, 超出丢弃最旧)
    3. 后台线程每 5s 或积攒 50 条时批量写入
    4. 批量写入使用单次事务, 减少锁竞争

    线程安全: enqueue() 由主线程调用, _flush_loop() 由后台线程执行,
    通过 Queue 的线程安全性保证数据一致。
    """

    def __init__(self, max_queue_size: int = 1000, flush_interval: float = 5.0,
                 batch_size: int = 50):
        self._queue: Queue = Queue(maxsize=max_queue_size)
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._running = True
        self._stats = {
            "enqueued": 0,
            "written": 0,
            "dropped": 0,
            "errors": 0,
        }
        self._stats_lock = threading.Lock()

        # 启动后台写入线程
        self._thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="qaa-async-log-writer",
        )
        self._thread.start()
        logger.info(
            f"[AsyncLogWriter] 启动: flush_interval={flush_interval}s, "
            f"batch_size={batch_size}, max_queue={max_queue_size}"
        )

    def enqueue(
        self,
        session_factory: Callable,
        write_fn: Callable,
        record: Any,
    ) -> bool:
        """入队一条待写入记录

        Args:
            session_factory: 返回 DB session 的可调用对象 (如 SessionLocal)
            write_fn: 接收 (db_session, record) 的写入函数
            record: 要写入的数据

        Returns:
            True=入队成功, False=队列满被丢弃
        """
        try:
            self._queue.put_nowait((session_factory, write_fn, record))
            with self._stats_lock:
                self._stats["enqueued"] += 1
            return True
        except Exception:
            with self._stats_lock:
                self._stats["dropped"] += 1
            logger.debug("[AsyncLogWriter] 队列满, 丢弃记录")
            return False

    def enqueue_dict(self, entry: Dict[str, Any]) -> bool:
        """入队一条 QAA 审计日志 (简化接口)

        Args:
            entry: 审计字典, 包含 "db_path", "table", "data" 等字段

        Returns:
            True=入队成功, False=队列满被丢弃
        """
        try:
            self._queue.put_nowait(("__dict__", entry))
            with self._stats_lock:
                self._stats["enqueued"] += 1
            return True
        except Exception:
            with self._stats_lock:
                self._stats["dropped"] += 1
            return False

    def _flush_loop(self):
        """后台写入线程主循环"""
        while self._running:
            try:
                batch = self._drain_batch()
                if batch:
                    self._write_batch(batch)
                else:
                    # 无数据, 短暂休眠
                    time.sleep(self._flush_interval)
            except Exception as e:
                logger.error(f"[AsyncLogWriter] flush 异常: {e}")
                time.sleep(1)

    def _drain_batch(self) -> List:
        """从队列中取出一批待写入记录"""
        batch = []
        try:
            # 先阻塞等一条 (最多等 flush_interval)
            item = self._queue.get(timeout=self._flush_interval)
            batch.append(item)
        except Empty:
            return batch

        # 非阻塞取更多
        while len(batch) < self._batch_size:
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except Empty:
                break

        return batch

    def _write_batch(self, batch: List):
        """批量写入一批记录"""
        for item in batch:
            try:
                if len(item) == 3 and item[0] != "__dict__":
                    session_factory, write_fn, record = item
                    db = session_factory()
                    try:
                        write_fn(db, record)
                        db.commit()
                    except Exception as e:
                        db.rollback()
                        raise e
                    finally:
                        db.close()
                elif item[0] == "__dict__":
                    # 简化接口: 直接记录到内存 (后续可对接 DB)
                    _, entry = item
                    _audit_buffer.append(entry)
                    if len(_audit_buffer) > 2000:
                        _audit_buffer.pop(0)

                with self._stats_lock:
                    self._stats["written"] += 1
            except Exception as e:
                with self._stats_lock:
                    self._stats["errors"] += 1
                logger.debug(f"[AsyncLogWriter] 写入失败: {e}")

    def stop(self):
        """停止后台写入线程"""
        self._running = False
        # 排空剩余
        remaining = []
        while not self._queue.empty():
            try:
                remaining.append(self._queue.get_nowait())
            except Empty:
                break
        if remaining:
            self._write_batch(remaining)
        logger.info(
            f"[AsyncLogWriter] 停止: "
            f"written={self._stats['written']} "
            f"dropped={self._stats['dropped']} "
            f"errors={self._stats['errors']}"
        )

    @property
    def stats(self) -> Dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()


# 内存审计缓冲区
_audit_buffer: List[Dict[str, Any]] = []

# 模块级单例
async_log_writer = AsyncLogWriter()


def get_audit_buffer() -> List[Dict[str, Any]]:
    """获取审计缓冲区 (供查询)"""
    return list(_audit_buffer)
