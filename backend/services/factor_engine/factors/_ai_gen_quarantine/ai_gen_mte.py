"""AI因子: 动量趋势衰竭因子 | 置信:65% | 通过长期与短期动量背离及RSI超买超卖状态，识别趋势末端衰竭风险。当短期动量与长期动量方向相反，且RSI出现顶/底背离迹象时，输出负值表示多头衰竭风险（避免做多），正值表示空头衰竭风险（避免做空）。旨在过滤持仓超时导致的反转亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumTrendExhaustion(BaseFactor):
    """通过长期与短期动量背离及RSI超买超卖状态，识别趋势末端衰竭风险。当短期动量与长期动量方向相反，且RSI出现顶/底背离迹象时，输出负值表示多头衰竭风险（避免做多），正值表示空头衰竭风险（避免做空）。旨在过滤持仓超时导致的反转亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mte",
            name="Momentum Trend Exhaustion",
            display_name="动量趋势衰竭因子",
            description="通过长期与短期动量背离及RSI超买超卖状态，识别趋势末端衰竭风险。当短期动量与长期动量方向相反，且RSI出现顶/底背离迹象时，输出负值表示多头衰竭风险（避免做多），正值表示空头衰竭风险（避免做空）。旨在过滤持仓超时导致的反转亏损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 长期和短期ROC
        roc_long = close.pct_change(20)
        roc_short = close.pct_change(5)
        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        # RSI背离简化：短期ROC与长期ROC方向相反，且RSI处于超买/超卖区域
        condition_bearish = (roc_short < 0) & (roc_long > 0) & (rsi > 70)
        condition_bullish = (roc_short > 0) & (roc_long < 0) & (rsi < 30)
        # 信号强度
        strength_bearish = (rsi - 70) / 30  # 0到1
        strength_bullish = (30 - rsi) / 30
        result = pd.Series(0.0, index=data.index)
        result[condition_bearish] = -strength_bearish[condition_bearish].clip(0,1)
        result[condition_bullish] = strength_bullish[condition_bullish].clip(0,1)
        # 平滑处理
        result = result.rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
