"""AI因子: 流动性异常因子 | 置信:60% | 检测成交量与价格变动方向是否出现异常背离。当成交量显著放大但价格未同步移动（或反向移动），暗示可能为流动性陷阱或主力行为，适合在未知环境下做反向判断。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityAnomalyFactor(BaseFactor):
    """检测成交量与价格变动方向是否出现异常背离。当成交量显著放大但价格未同步移动（或反向移动），暗示可能为流动性陷阱或主力行为，适合在未知环境下做反向判断。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_anomaly",
            name="Liquidity Anomaly Factor",
            display_name="流动性异常因子",
            description="检测成交量与价格变动方向是否出现异常背离。当成交量显著放大但价格未同步移动（或反向移动），暗示可能为流动性陷阱或主力行为，适合在未知环境下做反向判断。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 价格变化方向（1周期）
        price_dir = np.sign(close.diff())
        # 成交量变化方向
        vol_dir = np.sign(volume.diff())
        # 计算成交量异常：成交量相对于近期均值的偏离
        vol_mean = volume.rolling(20).mean()
        vol_ratio = volume / vol_mean
        # 价格变化绝对值
        price_move = close.pct_change().abs()
        # 当成交量放大但价格变动很小（或反向）时，视为异常
        anomaly = vol_ratio * (1 - price_move / price_move.rolling(20).max())
        # 方向一致性：若价格与成交量方向相反，加重异常
        direction_conflict = (price_dir != vol_dir).astype(float)
        result = -np.tanh((anomaly * direction_conflict).fillna(0) * 1.5)
        return result
