"""AI因子: 持仓超时风险因子 | 置信:60% | 判断当前价格是否处于“无聊”状态，即价格在窄幅区间内波动且趋势不明显，容易导致持仓超时（hold_timeout）出场。该因子基于布林带宽度和价格变化率的综合指标，值越低（接近-1）表示市场越沉闷，应避免开仓；值越高（接近+1）表示有明确方向，适合持仓。可减少因市场无方向导致的超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRisk(BaseFactor):
    """判断当前价格是否处于“无聊”状态，即价格在窄幅区间内波动且趋势不明显，容易导致持仓超时（hold_timeout）出场。该因子基于布林带宽度和价格变化率的综合指标，值越低（接近-1）表示市场越沉闷，应避免开仓；值越高（接近+1）表示有明确方向，适合持仓。可减少因市场无方向导致的超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_htr",
            name="Hold Timeout Risk",
            display_name="持仓超时风险因子",
            description="判断当前价格是否处于“无聊”状态，即价格在窄幅区间内波动且趋势不明显，容易导致持仓超时（hold_timeout）出场。该因子基于布林带宽度和价格变化率的综合指标，值越低（接近-1）表示市场越沉闷，应避免开仓；值越高（接近+1）表示有明确方向，适合持仓。可减少因市场无方向导致的超时亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        # 布林带带宽
        sma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        bb_width = (2 * std) / sma  # 相对带宽
        # 价格变化率（最近N根K线的平均绝对变化）
        pct_change = close.pct_change().abs().rolling(window=period).mean()
        # 衡量沉闷程度：带宽越小、变化率越低，则越无聊
        # 将两个指标结合并归一化
        # 先计算分位数，然后取均值
        width_rank = bb_width.rank(pct=True)  # 0~1
        change_rank = pct_change.rank(pct=True)
        # 综合得分：两者平均后映射到[-1,1]
        composite = (width_rank + change_rank) / 2.0  # 0~1
        # 将0~1映射到-1~1 (0.5处为0)
        result = (composite - 0.5) * 2.0
        return result.fillna(0.0)
