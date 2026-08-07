"""可视化策略编译器 — Phase 2 存根（已废弃）"""
import logging
logger = logging.getLogger(__name__)


def compile_visual_strategy(*args, **kwargs):
    logger.warning("compile_visual_strategy called on stub — Phase 2 deprecated")
    return {"success": False, "error": "可视化策略编译器已在 Phase 2 移除"}


class VisualStrategyCompiler:
    def compile(self, *args, **kwargs):
        return compile_visual_strategy()
