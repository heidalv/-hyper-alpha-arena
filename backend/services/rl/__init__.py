"""Phase 7 DRL 包级导出。

[2026-09-02] 本文件自初始提交起为空，`from backend.services.rl import TradingEnv`
（test_phase7_drl 集成用例及任何依赖包级导入的调用方）一直 ImportError。
各子模块彼此独立，这里只做惰性安全导出：任一子模块导入失败不拖垮整个包。
"""
from __future__ import annotations

__all__ = [
    "TradingEnv",
    "HAS_GYM",
    "RLPolicyOptimizer",
    "KellyPositionSizer",
    "KellyPositionResult",
]

try:
    from backend.services.rl.trading_env import TradingEnv, HAS_GYM
except Exception:  # pragma: no cover - 缺 gym 等可选依赖时保持包可导入
    TradingEnv = None  # type: ignore[assignment]
    HAS_GYM = False

try:
    from backend.services.rl.rl_optimizer import RLPolicyOptimizer
except Exception:  # pragma: no cover
    RLPolicyOptimizer = None  # type: ignore[assignment]

try:
    from backend.services.rl.kelly_position_sizer import (
        KellyPositionResult,
        KellyPositionSizer,
    )
except Exception:  # pragma: no cover
    KellyPositionSizer = None  # type: ignore[assignment]
    KellyPositionResult = None  # type: ignore[assignment]
