"""BackendRegistry 统一入口 — 消除双 import 路径导致空注册表。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_registry():
    """返回已加载的 BackendRegistry 单例；必要时触发 load_all。"""
    try:
        from backend.services.learning import registry
    except ImportError:
        from services.learning import registry  # type: ignore

    if not registry.list_backends():
        try:
            from backend.services.learning.backend_loader import backend_loader
            backend_loader.load_all()
        except Exception as exc:
            logger.warning("[LearningRegistryBridge] load_all 失败: %s", exc)
    return registry
