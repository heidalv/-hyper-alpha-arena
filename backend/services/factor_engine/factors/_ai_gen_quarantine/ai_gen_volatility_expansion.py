"""AI因子: 波动率扩张风险 | 置信:60% | 衡量当前波动率相对于历史水平的异常扩张程度，捕捉可能导致止损或持仓超时的剧烈波动环境。正值表示波动率急剧放大(风险上升，不宜追趋势)，负值表示波动率收缩。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionHazard(BaseFactor):
    """衡量当前波动率相对于历史水平的异常扩张程度，捕捉可能导致止损或持仓超时的剧烈波动环境。正值表示波动率急剧放大(风险上升，不宜追趋势)，负值表示波动率收缩。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_expansion",
            name="Volatility Expansion Hazard",
            display_name="波动率扩张风险",
            description="衡量当前波动率相对于历史水平的异常扩张程度，捕捉可能导致止损或持仓超时的剧烈波动环境。正值表示波动率急剧放大(风险上升，不宜追趋势)，负值表示波动率收缩。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 真实波幅ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ma = atr.rolling(50).mean()
        atr_std = atr.rolling(50).std()
        # ATR偏离
        z_atr = (atr - atr_ma) / atr_std
        # 映射到[-1,1]，使用双曲正切
        score = np.tanh(z_atr)
        return pd.Series(score, index=data.index).fillna(0)
