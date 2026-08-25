"""AI因子: 市场状态清晰度因子 | 置信:60% | 衡量多时间框架趋势一致性的因子。当短期、中期、长期均线方向一致时，市场状态清晰，不易产生虚假信号；当方向混乱时（regime unknown），因子值趋于负值，提示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeClarityFactor(BaseFactor):
    """衡量多时间框架趋势一致性的因子。当短期、中期、长期均线方向一致时，市场状态清晰，不易产生虚假信号；当方向混乱时（regime unknown），因子值趋于负值，提示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_clarity",
            name="Regime Clarity Factor",
            display_name="市场状态清晰度因子",
            description="衡量多时间框架趋势一致性的因子。当短期、中期、长期均线方向一致时，市场状态清晰，不易产生虚假信号；当方向混乱时（regime unknown），因子值趋于负值，提示风险。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data: DataFrame with columns open, high, low, close, volume
        close = data['close']
        # 计算短期、中期、长期均线
        ma_short = close.rolling(10).mean()
        ma_mid = close.rolling(30).mean()
        ma_long = close.rolling(60).mean()
        # 计算均线间的距离标准化
        spread1 = (ma_short - ma_mid) / close
        spread2 = (ma_mid - ma_long) / close
        # 方向一致性：两个spread同号且绝对值大则清晰
        clarity = spread1 * spread2
        # 使用tanh压缩到[-1,1]并取负号，使负值表示混乱
        result = -np.tanh(5 * clarity)
        return result.fillna(0)
