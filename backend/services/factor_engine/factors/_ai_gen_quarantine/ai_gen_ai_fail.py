"""AI因子: AI反向失败 | 置信:60% | 模拟AI交易信号失败的情景：当短期趋势（如3周期动量）极强且成交量放大时，市场容易发生反向突袭。因子计算近期动量与成交量异常结合，然后度量后续反向概率。正值表示短期超买后预期下跌，负值表示短期超卖后预期上涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AIReverseFailure(BaseFactor):
    """模拟AI交易信号失败的情景：当短期趋势（如3周期动量）极强且成交量放大时，市场容易发生反向突袭。因子计算近期动量与成交量异常结合，然后度量后续反向概率。正值表示短期超买后预期下跌，负值表示短期超卖后预期上涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ai_fail",
            name="AI Reverse Failure",
            display_name="AI反向失败",
            description="模拟AI交易信号失败的情景：当短期趋势（如3周期动量）极强且成交量放大时，市场容易发生反向突袭。因子计算近期动量与成交量异常结合，然后度量后续反向概率。正值表示短期超买后预期下跌，负值表示短期超卖后预期上涨。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 短期动量
        mom = df['close'].pct_change(3)
        # 成交量放大
        vol_ma = df['volume'].rolling(10).mean()
        vol_spike = df['volume'] / vol_ma
        # 极端动量+放量
        extreme_up = (mom > 0.03) & (vol_spike > 1.5)
        extreme_dn = (mom < -0.03) & (vol_spike > 1.5)
        # 后续反转（下一期收盘相对于当前的变化）
        next_ret = df['close'].shift(-1) / df['close'] - 1
        # 信号：超买后下跌为正，超卖后上涨为负
        signal = pd.Series(0.0, index=df.index)
        signal[extreme_up] = -np.sign(next_ret[extreme_up]) * 1.0  # 如果下跌则正
        signal[extreme_dn] = np.sign(next_ret[extreme_dn]) * 1.0   # 如果上涨则负
        # 平滑
        result = signal.rolling(2, min_periods=1).mean().fillna(0)
        return result.clip(-1, 1)
