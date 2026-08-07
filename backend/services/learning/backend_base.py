"""
LearningBackend — 统一学习后端抽象基类

对标因子系统的 ``BaseFactor``：所有学习后端实现同一接口，
通过注册表统一调度，新增后端无需改动 process_outcome。
"""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LearningBackend(ABC):
    """统一学习后端接口。

    子类需设置：
        name:     后端唯一标识（用于注册表 key、日志、状态查询）
        priority: 调度顺序（小→先执行），默认 100

    子类至少实现 ``handle_outcome``。``should_trigger`` 默认返回 ``enabled``，
    需要更细粒度触发条件（如「仅亏损时」）时覆盖之。

    设计原则：
    - 零耦合：后端只处理 outcome，不感知其他后端存在
    - 非阻塞：单个后端抛异常不影响其他后端和主交易流程
    - 可观测：每次触发记录 debug 日志，便于排障
    """

    name: str = "base"
    priority: int = 100

    def __init__(self) -> None:
        # enabled 默认读配置；子类可在 __init__ 里按 env/settings 覆盖。
        # 延迟读取以避免 import 时 settings 未就绪。
        self._enabled_cache: Any = None

    # ── 启用状态 ──────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        """后端是否启用。子类可覆盖以读 env/settings。"""
        return True

    # ── 触发判断 ──────────────────────────────────────────
    def should_trigger(self, db: Session, outcome: Any) -> bool:
        """是否应处理本次 outcome。默认随 enabled。"""
        return self.enabled

    # ── 处理 ──────────────────────────────────────────────
    @abstractmethod
    def handle_outcome(self, db: Session, outcome: Any) -> None:
        """处理交易结果。必须自行 try/except 吞异常，避免影响其他后端。

        约定：此方法不应抛异常；内部失败记 debug/warning 日志即可。
        """
        raise NotImplementedError

    # ── 元信息 ────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        """返回后端状态摘要，供 dashboard 展示。"""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} enabled={self.enabled}>"


class AsyncBackend(LearningBackend):
    """异步学习后端基类。

    用于耗时较长的后端（如因果发现、LLM 反思）：
    ``handle_outcome`` 在后台守护线程执行，不阻塞主交易流程。

    子类实现 ``_run``（实际工作），本基类负责起线程、异常兜底、session 生命周期。
    """

    daemon: bool = True

    @abstractmethod
    def _run(self, db: Session, outcome: Any) -> None:
        """子类实现实际工作。在独立线程、独立 session 中执行。"""
        raise NotImplementedError

    def handle_outcome(self, db: Session, outcome: Any) -> None:
        """起后台线程执行 _run，自带独立 session。"""
        # 延迟 import 避免循环依赖
        from backend.database.connection import SessionLocal

        def _worker() -> None:
            worker_db = SessionLocal()
            try:
                self._run(worker_db, outcome)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[%s] 后台任务失败: %s", self.name, e, exc_info=True
                )
            finally:
                try:
                    worker_db.close()
                except Exception:
                    pass

        threading.Thread(
            target=_worker,
            daemon=self.daemon,
            name=f"learn-{self.name}",
        ).start()


class ThresholdBackend(LearningBackend):
    """按交易笔数阈值触发的后端基类。

    供 ReviewBackend / MinerBackend 等继承，统一管理「每 N 笔触发」逻辑 +
    冷却时间，取代旧 LearningBus 里散落的计数器。
    """

    threshold: int = 1          # 每多少笔触发一次
    cooldown_seconds: float = 0  # 最小冷却间隔（秒），0=不限

    def __init__(self) -> None:
        super().__init__()
        self._count_since_trigger: int = 0
        self._last_trigger_at: Any = None  # datetime | None

    def should_trigger(self, db: Session, outcome: Any) -> bool:
        if not self.enabled:
            return False
        # partial 平仓不计数（迁移自 paper_trading_engine 的 partial 跳过逻辑）
        if _is_partial_outcome(outcome):
            return False
        self._count_since_trigger += 1
        if self._count_since_trigger < self.threshold:
            return False
        # 冷却检查
        if self._last_trigger_at is not None and self.cooldown_seconds > 0:
            from datetime import datetime, timezone
            elapsed = (datetime.now(timezone.utc) - self._last_trigger_at).total_seconds()
            if elapsed < self.cooldown_seconds:
                return False
        return True

    def handle_outcome(self, db: Session, outcome: Any) -> None:
        try:
            self._on_trigger(db, outcome)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] 触发执行失败: %s", self.name, e)
            return
        # 成功后重置计数
        from datetime import datetime, timezone
        self._count_since_trigger = 0
        self._last_trigger_at = datetime.now(timezone.utc)

    @abstractmethod
    def _on_trigger(self, db: Session, outcome: Any) -> None:
        """达到阈值时的实际工作。子类实现。"""
        raise NotImplementedError


def _is_partial_outcome(outcome: Any) -> bool:
    """判断是否为部分平仓（partial close）。

    partial 不触发计数型后端（review/miner），避免一笔仓位分多次平仓时频繁触发。
    """
    try:
        meta = getattr(outcome, "metadata", None)
        if isinstance(meta, dict):
            return bool(meta.get("partial_close", False))
    except Exception:
        pass
    return False
