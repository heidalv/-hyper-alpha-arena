"""
BackendRegistry — 学习后端注册表

对标因子系统的 ``FactorRegistry``：统一管理所有 LearningBackend，
提供 ``handle_all`` 让 process_outcome 一行调度全部后端。

单例模式，线程安全。
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Type

from sqlalchemy.orm import Session

from .backend_base import LearningBackend

logger = logging.getLogger(__name__)


class BackendRegistry:
    """学习后端注册表（单例）。"""

    _instance: Optional["BackendRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "BackendRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._backends: Dict[str, LearningBackend] = {}
        self._initialized = True
        logger.info("[BackendRegistry] 学习后端注册表初始化完成")

    # ── 注册 ──────────────────────────────────────────────
    def register(self, backend: LearningBackend, override: bool = False) -> None:
        """注册后端实例。

        Args:
            backend: LearningBackend 实例
            override: 是否覆盖同名后端
        """
        if not isinstance(backend, LearningBackend):
            raise TypeError(f"{backend} must be a LearningBackend instance")
        bid = backend.name
        if not bid:
            raise ValueError(f"Backend {backend.__class__.__name__} has empty name")
        if bid in self._backends and not override:
            logger.warning(
                "[BackendRegistry] 后端 '%s' 已注册，跳过重复注册 "
                "(override=True 可覆盖)", bid
            )
            return
        self._backends[bid] = backend
        logger.info(
            "[BackendRegistry] 注册后端: %s (priority=%s, enabled=%s)",
            bid, backend.priority, backend.enabled,
        )

    def unregister(self, name: str) -> None:
        if name in self._backends:
            del self._backends[name]
            logger.info("[BackendRegistry] 注销后端: %s", name)

    def get(self, name: str) -> Optional[LearningBackend]:
        return self._backends.get(name)

    def list_backends(self) -> List[LearningBackend]:
        """按 priority 升序列出所有后端。"""
        return sorted(self._backends.values(), key=lambda b: b.priority)

    def names(self) -> List[str]:
        return [b.name for b in self.list_backends()]

    def count(self) -> int:
        return len(self._backends)

    # ── 调度 ──────────────────────────────────────────────
    def handle_all(self, db: Session, outcome) -> Dict[str, bool]:
        """遍历所有已注册后端，按触发条件处理 outcome。

        - 按 priority 升序依次执行
        - 单个后端异常不影响其他后端（后端内部已吞异常，此处双重保险）
        - 返回 {backend_name: triggered} 状态字典

        约定：本方法不抛异常，确保 process_outcome 主流程不受后端影响。
        """
        result: Dict[str, bool] = {}
        for backend in self.list_backends():
            try:
                triggered = backend.should_trigger(db, outcome)
                if not triggered:
                    result[backend.name] = False
                    continue
                backend.handle_outcome(db, outcome)
                result[backend.name] = True
            except Exception as e:  # noqa: BLE001  双重保险，永不外泄
                logger.debug(
                    "[BackendRegistry] 后端 %s 处理跳过: %s", backend.name, e
                )
                result[backend.name] = False
        return result

    def status(self) -> List[Dict]:
        """所有后端状态摘要，供 dashboard。"""
        return [b.status() for b in self.list_backends()]

    def clear(self) -> None:
        self._backends.clear()
        logger.warning("[BackendRegistry] 已清空全部后端")


# 全局单例
registry = BackendRegistry()


def register_backend(override: bool = False):
    """后端注册装饰器。

    使用示例::

        @register_backend()
        class MyBackend(LearningBackend):
            name = "my_backend"
            def handle_outcome(self, db, outcome): ...

    注意：因 LearningBackend 多为有状态单例（计数器/缓存），装饰器会实例化类。
    若类已在别处实例化并注册，此处会跳过（override=False）。
    """
    def decorator(backend_cls: Type[LearningBackend]):
        try:
            instance = backend_cls()
            registry.register(instance, override=override)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[BackendRegistry] 注册 %s 失败: %s", backend_cls.__name__, e
            )
        return backend_cls
    return decorator
