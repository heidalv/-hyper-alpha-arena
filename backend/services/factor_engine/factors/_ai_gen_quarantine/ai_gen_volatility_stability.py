"""AI因子: 波动稳定性指数 | 置信:50% | 计算近期（10日）ATR与长期（50日）ATR的比值，衡量波动率稳定性。比值越偏离1，表示波动环境发生突变（regime unknown风险高），通过1-|ratio-1|线性映射后转为[-1,1]输出。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStabilityIndex(BaseFactor):
    """计算近期（10日）ATR与长期（50日）ATR的比值，衡量波动率稳定性。比值越偏离1，表示波动环境发生突变（regime unknown风险高），通过1-|ratio-1|线性映射后转为[-1,1]输出。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_stability",
            name="Volatility Stability Index",
            display_name="波动稳定性指数",
            description="计算近期（10日）ATR与长期（50日）ATR的比值，衡量波动率稳定性。比值越偏离1，表示波动环境发生突变（regime unknown风险高），通过1-|ratio-1|线性映射后转为[-1,1]输出。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.abs(high - prev_close), np.abs(low - prev_close))
        atr_short = tr.rolling(10).mean()
        atr_long = tr.rolling(50).mean()
        ratio = atr_short / atr_long
        # 映射: 1 - |ratio-1|, 再归一化到[-1,1]
        stability = 1 - np.abs(ratio - 1)
        result = 2 * stability - 1
        return result.fillna(0).clip(-1, 1)
