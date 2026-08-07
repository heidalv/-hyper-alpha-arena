"""AI因子: 自适应区间震荡因子 | 置信:60% | 通过布林带宽度与RSI组合识别市场是否处于窄幅震荡状态。当布林带宽度收缩且RSI在中间区域时，市场缺乏明确方向，因子为负，提示避免趋势策略。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Adaptive_Range_Oscillator(BaseFactor):
    """通过布林带宽度与RSI组合识别市场是否处于窄幅震荡状态。当布林带宽度收缩且RSI在中间区域时，市场缺乏明确方向，因子为负，提示避免趋势策略。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_aro",
            name="Adaptive Range Oscillator",
            display_name="自适应区间震荡因子",
            description="通过布林带宽度与RSI组合识别市场是否处于窄幅震荡状态。当布林带宽度收缩且RSI在中间区域时，市场缺乏明确方向，因子为负，提示避免趋势策略。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 布林带带宽 (20,2)
        ma20 = data['close'].rolling(20).mean()
        std20 = data['close'].rolling(20).std()
        bandwidth = (2 * std20) / ma20  # 相对带宽
        # RSI (14)
        delta = data['close'].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        # 震荡条件：带宽小于历史均值且RSI在40-60之间
        bw_ma = bandwidth.rolling(50).mean()
        bw_condition = (bandwidth < bw_ma).astype(float)
        rsi_condition = ((rsi > 40) & (rsi < 60)).astype(float)
        # 综合得分 (0~1)，取负值表示不适合趋势
        raw = (bw_condition + rsi_condition) / 2.0  # 0~1
        # 映射到[-1,0]区间：震荡越强越负
        result = -raw * 1.0
        return result.fillna(0)
