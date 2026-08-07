"""
RL 优化器单例（P0-4）

场景：`rl_routes.py` 与 `SystemCoordinator` 原本各自维护一份 `RLPolicyOptimizer`
实例，导致训练与推理不共享、重启后模型不持久。统一通过本模块访问单例，
且在首次创建时若磁盘存在 `models/drl/ppo_latest.zip` 自动 load。

使用方式：
    from backend.services.rl.rl_singleton import get_rl_optimizer, reload_from_disk
    opt = get_rl_optimizer()          # 全局共享实例
    reload_from_disk()                # 强制重新从磁盘加载（训练完成后触发）
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_optimizer: Optional["RLPolicyOptimizer"] = None  # type: ignore[name-defined]
_initial_load_attempted = False


def get_rl_optimizer():
    """返回全局唯一的 `RLPolicyOptimizer` 实例；首次调用时尝试从磁盘加载。"""
    global _optimizer, _initial_load_attempted
    if _optimizer is not None:
        return _optimizer

    with _lock:
        if _optimizer is not None:
            return _optimizer
        try:
            from backend.services.rl.rl_optimizer import RLPolicyOptimizer
            inst = RLPolicyOptimizer()
        except Exception as e:
            logger.warning(f"[rl_singleton] RLPolicyOptimizer 初始化失败: {e}")
            return None

        # 首次创建尝试加载磁盘模型（失败不致命，保持随机策略）
        if not _initial_load_attempted:
            _initial_load_attempted = True
            try:
                loaded = inst.load()
                if loaded:
                    logger.info("[rl_singleton] 自动加载磁盘模型成功")
                else:
                    logger.info("[rl_singleton] 无磁盘模型或加载失败，使用随机/未训练策略")
            except Exception as e:
                logger.warning(f"[rl_singleton] 自动加载模型异常: {e}")

        _optimizer = inst
        return _optimizer


def reload_from_disk() -> bool:
    """外部训练完成后强制重新 load（不替换实例引用，仅刷新 model 权重）。"""
    inst = get_rl_optimizer()
    if inst is None:
        return False
    try:
        return inst.load()
    except Exception as e:
        logger.warning(f"[rl_singleton] reload_from_disk 异常: {e}")
        return False


def reset_for_test() -> None:
    """测试 hook：清除单例引用。仅供单元测试使用。"""
    global _optimizer, _initial_load_attempted
    with _lock:
        _optimizer = None
        _initial_load_attempted = False
