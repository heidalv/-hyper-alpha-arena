"""Rebate 套利策略注册表。

2026-07-06 修复（病灶A）：
    此前本目录缺少 __init__.py（隐式命名空间包），且 `ALL_STRATEGIES` 全库从未
    定义。engine.py / ai_config_generator.py 顶层 `from .strategies import
    ALL_STRATEGIES` 因此在运行时抛出：
        cannot import name 'ALL_STRATEGIES' from
        'backend.services.rebate_arb.strategies' (unknown location)
    连锁导致 `_run_rebate_arb_tick → run_qaa_rebate_tick → import rebate_arb_engine`
    每个 tick 都 ImportError 被静默吞掉——Rebate 套利实际完全空转，但前端域仍显示
    "已加载"。本模块显式构建 `ALL_STRATEGIES` 单例注册表，修复该问题。

设计约定：
    - key 为大写策略 ID（"S2".."S8"），与 RebateStrategyType 的 value 对齐，
      engine 里 `RebateStrategyType(strategy_id)` 可直接构造。
    - S1 / S5 已下线（负 EV / 数据结构 bug），刻意不注册（与 engine.py:307 注释
      "S1/S5 已下线且不在 ALL_STRATEGIES" 保持一致）。
    - 每个策略从 rebate_config 读取自己的 params 初始化；单个策略实例化失败不影响
      其余策略注册（容错，避免一个坏策略再次拖垮整个引擎）。
    - 是否"启用"由 engine._is_strategy_enabled + program_registry 存活自检控制，
      注册表本身只负责"可用的策略实例集合"。
"""

import logging
from typing import Any, Dict

from .s2_vip_sprint import S2VIPSprintStrategy
from .s3_points_mining import S3PointsMiningStrategy
from .s4_campaign_arb import S4CampaignArbStrategy
from .s7_binance_alpha import S7BinanceAlphaStrategy
from .s8_asterdex_rh import S8AsterdexRhStrategy
from .s_delta_neutral_points import DeltaNeutralPointsStrategy

logger = logging.getLogger(__name__)


def _strategy_params(strategy_id: str) -> Dict[str, Any]:
    """从 rebate_config 读取某策略的 params（不可用时返回空 dict = 用默认值）。"""
    try:
        from backend.config.rebate_config_loader import rebate_config

        return dict(rebate_config.get_strategy_config(strategy_id).params or {})
    except Exception as exc:  # 配置不可用时用策略内置默认值兜底
        logger.debug("[Strategies] %s 读取配置失败，用默认参数: %s", strategy_id, exc)
        return {}


# 已下线策略：不注册（负 EV / 数据结构 bug），仅保留源码供历史仓位解读。
DEPRECATED_STRATEGY_IDS = ("S1", "S5", "S6")

# (策略 ID, 策略类) —— 注册顺序即扫描顺序
_STRATEGY_CLASSES = (
    ("SDN", DeltaNeutralPointsStrategy),  # Phase2 新主力：delta-neutral 刷积分
    ("S2", S2VIPSprintStrategy),
    ("S3", S3PointsMiningStrategy),
    ("S4", S4CampaignArbStrategy),
    ("S7", S7BinanceAlphaStrategy),
    ("S8", S8AsterdexRhStrategy),
)


def build_all_strategies() -> Dict[str, Any]:
    """构建 {策略ID: 策略实例} 注册表（容错：单个失败跳过，不影响其余）。"""
    registry: Dict[str, Any] = {}
    for sid, cls in _STRATEGY_CLASSES:
        try:
            registry[sid] = cls(_strategy_params(sid))
        except Exception as exc:
            logger.warning("[Strategies] 策略 %s 实例化失败，已跳过注册: %s", sid, exc)
    logger.info(
        "[Strategies] ALL_STRATEGIES 就绪: %s（已下线未注册: %s）",
        list(registry.keys()),
        list(DEPRECATED_STRATEGY_IDS),
    )
    return registry


# 模块级单例注册表（engine.py / ai_config_generator.py 直接 import 使用）
ALL_STRATEGIES: Dict[str, Any] = build_all_strategies()

__all__ = [
    "ALL_STRATEGIES",
    "build_all_strategies",
    "DEPRECATED_STRATEGY_IDS",
    "S2VIPSprintStrategy",
    "S3PointsMiningStrategy",
    "S4CampaignArbStrategy",
    "S7BinanceAlphaStrategy",
    "S8AsterdexRhStrategy",
    "DeltaNeutralPointsStrategy",
]
