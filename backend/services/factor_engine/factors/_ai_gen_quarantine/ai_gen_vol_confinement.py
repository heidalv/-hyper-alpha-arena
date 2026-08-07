"""AI因子: 波动率收敛因子 | 置信:65% | 识别价格在窄幅区间内整理、波动率极低的时期。该状态下市场方向不明，容易触发止损或持仓超时亏损。因子计算近期ATR与历史ATR均值的比值，并叠加布林带宽度，当波动率显著低于常态时输出负值，提示避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Confinement(BaseFactor):
    """识别价格在窄幅区间内整理、波动率极低的时期。该状态下市场方向不明，容易触发止损或持仓超时亏损。因子计算近期ATR与历史ATR均值的比值，并叠加布林带宽度，当波动率显著低于常态时输出负值，提示避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_confinement",
            name="Volatility Confinement",
            display_name="波动率收敛因子",
            description="识别价格在窄幅区间内整理、波动率极低的时期。该状态下市场方向不明，容易触发止损或持仓超时亏损。因子计算近期ATR与历史ATR均值的比值，并叠加布林带宽度，当波动率显著低于常态时输出负值，提示避免做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']

        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()

        # 计算长周期ATR均值作为参考
        atr_long = atr.rolling(60).mean()

        # 波动率收缩比率
        vol_ratio = atr / atr_long
        # 布林带宽度
        sma_20 = close.rolling(20).mean()
        std_20 = close.rolling(20).std()
        bb_width = 2 * std_20 / sma_20

        # 综合得分: 当vol_ratio < 0.5且bb_width < 0.05时认为极度收敛
        score = -1.0 * ((vol_ratio < 0.5) & (bb_width < 0.05)).astype(float) + 1.0 * ((vol_ratio > 0.8) & (bb_width > 0.1)).astype(float)
        # 平滑处理
        result = score.rolling(3).mean().fillna(0).clip(-1,1)
        return result
