"""AI因子: 波动率稳定性指数 | 置信:60% | 衡量近期价格波动的一致性。使用过去N周期收盘价对数收益率的标准差与滚动均值标准差的比值。当波动率稳定时，因子值接近0；当波动率急剧上升时，因子值为正；急剧下降为负。用于识别市场状态是否清晰。正值表示波动加剧，可能预示趋势启动；负值表示波动萎缩，市场可能进入盘整。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStabilityIndex(BaseFactor):
    """衡量近期价格波动的一致性。使用过去N周期收盘价对数收益率的标准差与滚动均值标准差的比值。当波动率稳定时，因子值接近0；当波动率急剧上升时，因子值为正；急剧下降为负。用于识别市场状态是否清晰。正值表示波动加剧，可能预示趋势启动；负值表示波动萎缩，市场可能进入盘整。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volstability",
            name="Volatility Stability Index",
            display_name="波动率稳定性指数",
            description="衡量近期价格波动的一致性。使用过去N周期收盘价对数收益率的标准差与滚动均值标准差的比值。当波动率稳定时，因子值接近0；当波动率急剧上升时，因子值为正；急剧下降为负。用于识别市场状态是否清晰。正值表示波动加剧，可能预示趋势启动；负值表示波动萎缩，市场可能进入盘整。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        close = data['close']
        ret = np.log(close / close.shift(1))
        window = 20
        rolling_std = ret.rolling(window).std()
        # 计算滚动均值的标准差（稳定性）
        mean_std = rolling_std.rolling(window).mean()
        stability = (rolling_std - mean_std) / (mean_std + 1e-10)
        # 缩放到[-1,1]
        result = np.clip(stability, -1, 1)
        return result
