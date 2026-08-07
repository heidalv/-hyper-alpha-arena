"""自主策略服务 — Phase 2 存根（已废弃，由 full_auto_trading_service 替代）

Phase 2 重构说明：
- AutonomousStrategyService 的"分析-决策-交易循环"功能已由 full_auto_trading_service 统一承担。
- 此文件保留存根以避免现有引用点产生 ImportError，所有方法均为 no-op。
"""
import logging
import warnings
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

warnings.warn(
    "autonomous_strategy_service is deprecated (Phase 2), use FullAutoTradingService",
    DeprecationWarning, stacklevel=2,
)


class AutonomousStrategyService:
    """Phase 2 存根：自主策略服务已废弃，请使用 FullAutoTradingService。"""

    def __init__(self):
        logger.debug("AutonomousStrategyService stub initialized (Phase 2 — deprecated)")

    def activate_strategy(self, strategy_id: str, **kwargs) -> Dict[str, Any]:
        logger.warning(f"[Autonomous] activate_strategy({strategy_id}) called on stub — no-op")
        return {"success": False, "message": "AutonomousStrategyService 已废弃（Phase 2），请使用 FullAutoTradingService"}

    def deactivate_strategy(self, strategy_id: str) -> Dict[str, Any]:
        logger.warning(f"[Autonomous] deactivate_strategy({strategy_id}) called on stub — no-op")
        return {"success": False, "message": "AutonomousStrategyService 已废弃（Phase 2）"}

    def get_service_status(self) -> Dict[str, Any]:
            return {
            "active": False,
            "strategies": [],
            "message": "AutonomousStrategyService 已废弃（Phase 2）",
            }

    def get_strategy_status(self, strategy_id: str) -> Optional[Dict[str, Any]]:
                return None

    def stop_all(self):
        pass

    def __getattr__(self, name):
        """拦截所有其他方法调用，返回 no-op callable"""
        def _stub(*args, **kwargs):
            logger.debug(f"[Autonomous-stub] {name} called — no-op")
            return None
        return _stub


autonomous_service = AutonomousStrategyService()
