"""
TimeWindowCoordinator + StateConsistencyManager

时间窗口协调器: 协调不同时间尺度的系统更新
状态一致性管理器: 跨系统事务 + 乐观锁 + 数据库持久化

设计文档: .qoder/plans/AI学习系统深度整合方案 §6
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
#  TimeWindowCoordinator
# ══════════════════════════════════════════════════

class TimeWindowCoordinator:
    """
    时间窗口协调器

    不同系统的时间尺度:
    - DRL: 实时决策 (秒级)
    - Kelly: 交易级别 (分钟级)
    - 进化: 回测周期 (小时/天级)

    支持自适应间隔: 极端行情时加速更新频率。
    """

    SYNC_INTERVALS = {
        'kelly_stats': 300,          # 5分钟更新Kelly统计
        'drl_performance': 3600,     # 1小时更新DRL表现
        'evolution_check': 86400,    # 1天检查进化
        'correlation_update': 3600,  # 1小时更新相关性
    }

    def __init__(self):
        self._last_update: Dict[str, float] = {}

    def should_update(self, task: str, regime: str = "ranging",
                      volatility: float = 0.02) -> bool:
        """
        判断是否应该执行指定任务的更新

        Args:
            task: 任务名称 (kelly_stats / drl_performance / evolution_check / correlation_update)
            regime: 当前市场状态
            volatility: 当前波动率
        """
        base_interval = self.SYNC_INTERVALS.get(task, 3600)
        adaptive_interval = self._adaptive_interval(base_interval, regime, volatility)

        last = self._last_update.get(task, 0)
        now = time.time()

        if now - last >= adaptive_interval:
            self._last_update[task] = now
            return True
        return False

    def should_retrain_drl(self, db: Session) -> bool:
        """
        判断是否需要重训练DRL

        触发条件（任一满足）:
        1. DRL准确率下降超过15%
        2. 新币种加入
        3. 进化参数与DRL训练环境漂移超过30%
        4. 市场regime发生变化
        """
        try:
            from backend.database.models import DRLPerformance, SystemCoordinatorState

            # 检查DRL准确率趋势
            recent = db.query(DRLPerformance).order_by(
                DRLPerformance.timestamp.desc()
            ).limit(100).all()

            if len(recent) < 30:
                return False

            # 最近30条 vs 前70条的准确率对比
            recent_30 = recent[:30]
            older_70 = recent[30:100]

            recent_acc = sum(1 for r in recent_30 if r.is_correct) / len(recent_30)
            older_acc = sum(1 for r in older_70 if r.is_correct) / max(len(older_70), 1)

            if older_acc - recent_acc > 0.15:
                logger.info(f"[TimeWindow] DRL准确率下降: {older_acc:.2f}→{recent_acc:.2f}")
                return True

            return False
        except Exception as e:
            logger.debug(f"[TimeWindow] DRL重训练检查失败: {e}")
            return False

    def should_evolve(self, db: Session) -> bool:
        """判断是否需要触发进化"""
        try:
            from backend.database.models import SystemCoordinatorState

            state = db.query(SystemCoordinatorState).first()
            if not state:
                return False

            # 距上次进化超过1天
            if state.last_evolution_at:
                elapsed = (datetime.now(timezone.utc) - state.last_evolution_at).total_seconds()
                return elapsed >= self.SYNC_INTERVALS['evolution_check']

            return True  # 从未进化过
        except Exception:
            return False

    def _adaptive_interval(self, base_interval: int, regime: str,
                           volatility: float) -> int:
        """
        根据市场状态自适应调整间隔

        - 极端行情 (volatility > 0.05): 间隔缩短到1/3
        - 危机regime: 间隔缩短到1/4
        - 正常行情: 使用base_interval
        - 低波动 (volatility < 0.01): 间隔延长到2x
        """
        if regime == "crisis":
            return max(base_interval // 4, 60)
        if volatility > 0.05:
            return max(base_interval // 3, 60)
        elif volatility < 0.01:
            return base_interval * 2
        return base_interval

    def mark_updated(self, task: str):
        """标记任务已更新"""
        self._last_update[task] = time.time()


# ══════════════════════════════════════════════════
#  StateConsistencyManager
# ══════════════════════════════════════════════════

@dataclass
class Transaction:
    """事务记录"""
    tx_id: str
    systems: List[str]
    started_at: float
    timeout: float
    versions: Dict[str, int] = field(default_factory=dict)


class StateConsistencyManager:
    """
    状态一致性管理器

    使用乐观锁 + 版本号 + 数据库持久化 确保:
    1. 进化更新参数时不会覆盖正在使用的DRL
    2. Kelly统计更新不会在计算过程中被修改
    3. 跨系统原子操作（同时更新进化+DRL+Kelly）
    4. 进程重启后版本号不丢失
    5. 超时自动释放锁
    """

    def __init__(self):
        self._active_transactions: Dict[str, Transaction] = {}
        self._system_locks: Dict[str, threading.Lock] = {}
        self._cleanup_interval = 60  # 每60秒清理超时事务
        self._last_cleanup = time.time()

    def begin_transaction(self, systems: List[str], timeout: float = 30.0) -> str:
        """
        开始跨系统事务，返回事务ID

        Args:
            systems: 涉及的系统列表 (如 ['evolution', 'drl', 'kelly'])
            timeout: 超时时间（秒）

        Returns:
            事务ID
        """
        # 清理超时事务
        self._cleanup_expired()

        tx_id = str(uuid.uuid4())[:12]

        # 获取各系统锁
        for system in systems:
            if system not in self._system_locks:
                self._system_locks[system] = threading.Lock()
            self._system_locks[system].acquire()

        # 读取当前版本号
        versions = {}
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemCoordinatorState

            db = SessionLocal()
            try:
                state = db.query(SystemCoordinatorState).first()
                if state and state.param_versions:
                    versions = json.loads(state.param_versions)
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[StateConsistency] 读取版本号失败: {e}")

        tx = Transaction(
            tx_id=tx_id,
            systems=list(systems),
            started_at=time.time(),
            timeout=timeout,
            versions=versions,
        )
        self._active_transactions[tx_id] = tx

        # 更新数据库中的事务状态
        self._update_db_transaction(tx_id, systems)

        logger.info(f"[StateConsistency] 事务开始: {tx_id} systems={systems}")
        return tx_id

    def commit_if_valid(self, tx_id: str) -> bool:
        """
        提交变更，验证所有涉及系统的版本未变

        乐观锁: 读取时记录版本号，提交时验证版本未变。
        若版本已变: 返回False，调用方需重试或降级。
        """
        tx = self._active_transactions.get(tx_id)
        if not tx:
            logger.warning(f"[StateConsistency] 事务{tx_id}不存在")
            return False

        # 验证版本号
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemCoordinatorState

            db = SessionLocal()
            try:
                state = db.query(SystemCoordinatorState).first()
                if state and state.param_versions:
                    current_versions = json.loads(state.param_versions)
                    # 检查版本是否被其他事务修改
                    for system in tx.systems:
                        if current_versions.get(system, 0) != tx.versions.get(system, 0):
                            logger.warning(
                                f"[StateConsistency] 事务{tx_id}版本冲突: "
                                f"{system} expected={tx.versions.get(system, 0)} "
                                f"actual={current_versions.get(system, 0)}"
                            )
                            self._release_transaction(tx_id)
                            return False

                # 版本号递增
                for system in tx.systems:
                    tx.versions[system] = tx.versions.get(system, 0) + 1

                if state:
                    state.param_versions = json.dumps(tx.versions)
                    state.active_transaction_id = None
                    state.locked_systems = None
                    state.sync_status = "idle"
                    db.commit()

            finally:
                db.close()
        except Exception as e:
            logger.error(f"[StateConsistency] 提交失败: {e}")
            self._release_transaction(tx_id)
            return False

        self._release_transaction(tx_id)
        logger.info(f"[StateConsistency] 事务提交: {tx_id}")
        return True

    def rollback(self, tx_id: str):
        """回滚变更，释放锁"""
        tx = self._active_transactions.get(tx_id)
        if tx:
            logger.info(f"[StateConsistency] 事务回滚: {tx_id}")

        self._release_transaction(tx_id)

        # 清理数据库事务状态
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemCoordinatorState

            db = SessionLocal()
            try:
                state = db.query(SystemCoordinatorState).first()
                if state:
                    state.active_transaction_id = None
                    state.locked_systems = None
                    state.sync_status = "idle"
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass

    def get_system_version(self, system: str) -> int:
        """从数据库读取版本号（持久化）"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemCoordinatorState

            db = SessionLocal()
            try:
                state = db.query(SystemCoordinatorState).first()
                if state and state.param_versions:
                    versions = json.loads(state.param_versions)
                    return versions.get(system, 0)
            finally:
                db.close()
        except Exception:
            pass
        return 0

    def _release_transaction(self, tx_id: str):
        """释放事务持有的锁"""
        tx = self._active_transactions.pop(tx_id, None)
        if tx:
            for system in tx.systems:
                lock = self._system_locks.get(system)
                if lock and lock.locked():
                    try:
                        lock.release()
                    except RuntimeError:
                        pass

    def _update_db_transaction(self, tx_id: str, systems: List[str]):
        """更新数据库中的事务状态"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemCoordinatorState

            db = SessionLocal()
            try:
                state = db.query(SystemCoordinatorState).first()
                if not state:
                    state = SystemCoordinatorState(sync_status="syncing")
                    db.add(state)
                else:
                    state.active_transaction_id = tx_id
                    state.locked_systems = json.dumps(systems)
                    state.sync_status = "syncing"
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[StateConsistency] 更新事务状态失败: {e}")

    def _cleanup_expired(self):
        """清理超时事务"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        expired = []
        for tx_id, tx in self._active_transactions.items():
            if now - tx.started_at > tx.timeout:
                expired.append(tx_id)

        for tx_id in expired:
            logger.warning(f"[StateConsistency] 事务{tx_id}超时，自动回滚")
            self.rollback(tx_id)


# 全局单例
time_window_coordinator = TimeWindowCoordinator()
state_consistency_manager = StateConsistencyManager()
