"""AI因子: 回撤速度 | 置信:60% | 衡量价格从近期最高点回撤的速度和幅度，快速大幅回撤提示止损信号。负值越大表示回撤越危险，正值表示价格创新高或缓慢上升。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DrawdownSpeed(BaseFactor):
    """衡量价格从近期最高点回撤的速度和幅度，快速大幅回撤提示止损信号。负值越大表示回撤越危险，正值表示价格创新高或缓慢上升。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_drawdown_speed",
            name="Drawdown Speed",
            display_name="回撤速度",
            description="衡量价格从近期最高点回撤的速度和幅度，快速大幅回撤提示止损信号。负值越大表示回撤越危险，正值表示价格创新高或缓慢上升。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        high = data['high']
        close = data['close']
        # 计算过去10天的最高价
        recent_high = high.rolling(10).max()
        # 当前价格相对最高点的回撤百分比
        drawdown = (close - recent_high) / recent_high
        # 计算回撤的速度：当前回撤与过去5天平均回撤的变化
        drawdown_change = drawdown - drawdown.rolling(5).mean()
        # 组合：回撤深度 + 速度，负值越大越危险
        # 使用tanh压缩
        score = np.tanh((drawdown + drawdown_change) * 5)
        score = score.fillna(0)
        return score.clip(-1, 1)
