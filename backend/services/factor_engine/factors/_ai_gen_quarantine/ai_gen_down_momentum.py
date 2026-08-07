"""AI因子: 下跌动量因子 | 置信:60% | 通过短期价格动量与均线相对位置，识别价格处于下跌趋势且动量加速向下的状态，值越接近-1表示下跌风险越大，做多易触发止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Downward_Momentum_Factor(BaseFactor):
    """通过短期价格动量与均线相对位置，识别价格处于下跌趋势且动量加速向下的状态，值越接近-1表示下跌风险越大，做多易触发止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_down_momentum",
            name="Downward Momentum Factor",
            display_name="下跌动量因子",
            description="通过短期价格动量与均线相对位置，识别价格处于下跌趋势且动量加速向下的状态，值越接近-1表示下跌风险越大，做多易触发止损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 20日EMA作为趋势参考
        ema20 = close.ewm(span=20, adjust=False).mean()
        # 短期动量：5日变化率
        mom5 = close.pct_change(5)
        # 价格相对EMA位置：价格低于EMA记负值，低于越多负值越大
        rel_pos = (close - ema20) / ema20
        # 下跌动量：当mom5为负且rel_pos为负时，强化信号
        down_mom = -np.sign(mom5) * np.abs(mom5) * (rel_pos < 0).astype(int)
        # 归一化到[-1,1]：使用clip和tanh
        raw = -np.sign(mom5) * (np.abs(mom5) * 10 + (rel_pos < 0).astype(float) * 0.5)
        result = np.tanh(raw).clip(-1, 1)
        return result
