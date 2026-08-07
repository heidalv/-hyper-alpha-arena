"""AI因子: 日内波动反转识别 | 置信:55% | 基于短期价格波动率激增与当前方向相反的动量衰减，捕捉因AI逆转或急速拉升后的回调，适用于ai_reverse亏损模式。使用最高最低价幅度与收盘价相对位置。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class IntradayVolatilityReversal(BaseFactor):
    """基于短期价格波动率激增与当前方向相反的动量衰减，捕捉因AI逆转或急速拉升后的回调，适用于ai_reverse亏损模式。使用最高最低价幅度与收盘价相对位置。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_reverse",
            name="Intraday Volatility Reversal",
            display_name="日内波动反转识别",
            description="基于短期价格波动率激增与当前方向相反的动量衰减，捕捉因AI逆转或急速拉升后的回调，适用于ai_reverse亏损模式。使用最高最低价幅度与收盘价相对位置。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        # 日内波动率（高低差/收盘）
        vol = (high - low) / close
        vol_ma = vol.rolling(10).mean()
        vol_spike = (vol - vol_ma) / vol_ma  # 波动率偏离
        # 价格位置：收盘价在当日高低区间的相对位置（0~1）
        pos = (close - low) / (high - low + 1e-9)
        # 极端位置+波动率激增 => 反转信号
        extreme_high = (pos > 0.85) & (vol_spike > 0.5)
        extreme_low = (pos < 0.15) & (vol_spike > 0.5)
        result = pd.Series(0.0, index=data.index)
        result[extreme_high] = -1  # 高位波动大，看空反转
        result[extreme_low] = 1    # 低位波动大，看多反转
        return result.rolling(2).mean().fillna(0)
