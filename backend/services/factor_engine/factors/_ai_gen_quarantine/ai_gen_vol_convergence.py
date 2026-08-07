"""AI因子: 波动率收敛指标 | 置信:65% | 通过比较近期ATR与历史ATR的比值，识别市场进入窄幅震荡状态。当波动率显著收缩时，趋势策略易被假突破止损，该因子输出负值（建议做空趋势或避免交易）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityConvergenceIndicator(BaseFactor):
    """通过比较近期ATR与历史ATR的比值，识别市场进入窄幅震荡状态。当波动率显著收缩时，趋势策略易被假突破止损，该因子输出负值（建议做空趋势或避免交易）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_convergence",
            name="Volatility_Convergence_Indicator",
            display_name="波动率收敛指标",
            description="通过比较近期ATR与历史ATR的比值，识别市场进入窄幅震荡状态。当波动率显著收缩时，趋势策略易被假突破止损，该因子输出负值（建议做空趋势或避免交易）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        ratio = atr_short / atr_long
        # 当ratio小于0.6时表示波动率大幅收缩，因子为-1；大于1.2时放量为1，其余线性映射到[-1,1]
        ratio = ratio.fillna(1.0)
        factor = 2 * (ratio - 0.9) / 0.6  # 中心0.9，范围0.6~1.2映射到-1~1
        factor = np.clip(factor, -1, 1)
        return pd.Series(factor, index=data.index)
