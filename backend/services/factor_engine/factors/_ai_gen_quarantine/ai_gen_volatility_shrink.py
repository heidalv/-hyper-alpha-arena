"""AI因子: 波动率收缩指标 | 置信:70% | 基于近期ATR与历史ATR的比率，识别波动率正在快速收缩的市场环境。低波动环境下价格易出现假突破和频繁止损，对应亏损模式中的regime=unknown。输出正值表示波动率收缩（高亏损风险），负值表示波动率扩张。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Contraction_Indicator(BaseFactor):
    """基于近期ATR与历史ATR的比率，识别波动率正在快速收缩的市场环境。低波动环境下价格易出现假突破和频繁止损，对应亏损模式中的regime=unknown。输出正值表示波动率收缩（高亏损风险），负值表示波动率扩张。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_shrink",
            name="Volatility Contraction Indicator",
            display_name="波动率收缩指标",
            description="基于近期ATR与历史ATR的比率，识别波动率正在快速收缩的市场环境。低波动环境下价格易出现假突破和频繁止损，对应亏损模式中的regime=unknown。输出正值表示波动率收缩（高亏损风险），负值表示波动率扩张。",
            category="volatility",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        high, low, close = df['high'], df['low'], df['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        # 波动率收缩比率
        ratio = atr_short / (atr_long + 1e-10)
        # 当比率低于0.8表示收缩，高于1.2表示扩张
        # 映射到[-1,1]：中心化并使用阈值
        result = 1 - 2 * np.clip((ratio - 0.8) / (1.2 - 0.8), 0, 1)
        # 对NaN填充0
        result = result.fillna(0)
        return pd.Series(np.clip(result, -1, 1), index=df.index)
