"""
K线数据补漏管理器 - 处理后台补漏任务
"""

import asyncio
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from typing import Optional
import logging

from backend.database.connection import MarketSessionLocal
from backend.database.models import KlineCollectionTask
from .kline_data_service import kline_service

logger = logging.getLogger(__name__)

#: [2026-09-18 数据中心优化] 周期 → 分钟数。用于**按周期**估算应有 K 线根数。
_PERIOD_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080, "1M": 43200,
}


def expected_records_for(start: datetime, end: datetime, period: str) -> int:
    """按 `period` 估算窗口内应有的 K 线根数（至少 1）。

    [修] 原实现写死 `(end-start)/60`（**一律按 1 分钟**）⇒ 非 1m 任务的
    `total_records` 会成倍虚报：4h 虚报 **240×**、1d 虚报 **1440×**、1w 虚报 **10080×**。
    该字段会进 `kline_collection_tasks.total_records`，运维看"预计 N 条"时会被误导。
    （`progress` 是按**时间窗**算的，与本字段无关，故此前只有"预计条数"失真。）
    """
    try:
        mins = int(_PERIOD_MINUTES.get(str(period or "1m").strip(), 1))
    except Exception:  # noqa: BLE001
        mins = 1
    try:
        secs = (end - start).total_seconds()
    except Exception:  # noqa: BLE001
        return 1
    return max(1, int(secs / max(1, mins * 60)))


class BackfillManager:
    """补漏任务管理器"""

    def __init__(self):
        self.max_concurrent_tasks = 3  # 最大并发任务数

    async def process_task(self, task_id: int):
        """处理单个补漏任务"""
        logger.info(f"Starting backfill task {task_id}")

        with MarketSessionLocal() as db:
            # 获取任务信息
            task = db.query(KlineCollectionTask).filter(
                KlineCollectionTask.id == task_id
            ).first()

            if not task:
                logger.error(f"Task {task_id} not found")
                return

            if task.status != "pending":
                logger.warning(f"Task {task_id} is not pending (status: {task.status})")
                return

            try:
                # 更新任务状态为运行中
                task.status = "running"
                task.progress = 0
                db.commit()

                # 确保数据服务已初始化
                await kline_service.initialize()

                # 计算预期的记录数（**按周期**，非一律 1 分钟）
                time_diff = task.end_time - task.start_time
                expected_records = expected_records_for(
                    task.start_time, task.end_time, task.period)
                task.total_records = expected_records
                db.commit()

                logger.info(f"Task {task_id}: Collecting {expected_records} records for {task.symbol}")

                # 分批采集数据（每次最多6小时）
                batch_hours = 6
                current_start = task.start_time
                collected_total = 0

                while current_start < task.end_time:
                    # 计算当前批次的结束时间
                    current_end = min(
                        current_start + timedelta(hours=batch_hours),
                        task.end_time
                    )

                    logger.debug(f"Task {task_id}: Collecting batch {current_start} to {current_end}")

                    # 采集当前批次的数据
                    collected_batch = await kline_service.collect_historical_klines(
                        task.symbol,
                        current_start,
                        current_end,
                        task.period
                    )

                    collected_total += collected_batch

                    # 更新进度
                    progress = min(
                        int((current_end - task.start_time).total_seconds() / time_diff.total_seconds() * 100),
                        100
                    )

                    task.progress = progress
                    task.collected_records = collected_total
                    db.commit()

                    logger.debug(f"Task {task_id}: Progress {progress}%, collected {collected_batch} records")

                    # 移动到下一个批次
                    current_start = current_end

                    # 避免API限流
                    if current_start < task.end_time:
                        await asyncio.sleep(2)

                # 任务完成
                task.status = "completed"
                task.progress = 100
                task.collected_records = collected_total
                db.commit()

                logger.info(f"Task {task_id} completed successfully. Collected {collected_total} records.")

            except Exception as e:
                # 任务失败
                error_msg = str(e)
                logger.error(f"Task {task_id} failed: {error_msg}")

                task.status = "failed"
                task.error_message = error_msg
                db.commit()

    async def process_pending_tasks(self):
        """处理所有待处理的任务"""
        with MarketSessionLocal() as db:
            # 获取待处理的任务
            pending_tasks = db.query(KlineCollectionTask).filter(
                KlineCollectionTask.status == "pending"
            ).order_by(KlineCollectionTask.created_at).limit(self.max_concurrent_tasks).all()

            if not pending_tasks:
                logger.debug("No pending backfill tasks found")
                return

            logger.info(f"Processing {len(pending_tasks)} pending backfill tasks")

            # 并发处理任务
            tasks = []
            for task in pending_tasks:
                task_coroutine = asyncio.create_task(
                    self.process_task(task.id),
                    name=f"backfill_task_{task.id}"
                )
                tasks.append(task_coroutine)

            # 等待所有任务完成
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cleanup_old_tasks(self, days: int = 30):
        """清理旧的任务记录"""
        cutoff_date = datetime.now() - timedelta(days=days)

        with MarketSessionLocal() as db:
            # 删除30天前的已完成或失败任务
            deleted = db.query(KlineCollectionTask).filter(
                KlineCollectionTask.created_at < cutoff_date,
                KlineCollectionTask.status.in_(["completed", "failed"])
            ).delete()

            db.commit()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old backfill tasks")

    def get_task_status(self, task_id: int) -> Optional[dict]:
        """获取任务状态"""
        with MarketSessionLocal() as db:
            task = db.query(KlineCollectionTask).filter(
                KlineCollectionTask.id == task_id
            ).first()

            if not task:
                return None

            return {
                "task_id": task.id,
                "exchange": task.exchange,
                "symbol": task.symbol,
                "status": task.status,
                "progress": task.progress,
                "total_records": task.total_records or 0,
                "collected_records": task.collected_records or 0,
                "error_message": task.error_message,
                "created_at": task.created_at,
                "updated_at": task.updated_at
            }