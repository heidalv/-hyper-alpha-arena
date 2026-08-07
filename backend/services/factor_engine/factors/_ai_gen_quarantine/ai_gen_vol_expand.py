"""AI因子: 波动扩张止损风险因子 | 置信:80% | sl 止损亏损常发生在波动突然放大、价格反向跳动的环境。该因子通过日内振幅与近期波动率对比，检测异常扩张，数值为负表示止损风险升高，应降低仓位或收紧止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionStopLossRisk(BaseFactor):
    """sl 止损亏损常发生在波动突然放大、价格反向跳动的环境。该因子通过日内振幅与近期波动率对比，检测异常扩张，数值为负表示止损风险升高，应降低仓位或收紧止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_expand",
            name="Volatility Expansion / Stop Loss Risk",
            display_name="波动扩张止损风险因子",
            description="sl 止损亏损常发生在波动突然放大、价格反向跳动的环境。该因子通过日内振幅与近期波动率对比，检测异常扩张，数值为负表示止损风险升高，应降低仓位或收紧止损。",
            category="volatility",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        n = 14
        # 日内振幅
        tr = np.maximum(high - low, np.abs(high - np.roll(close, 1)))
        tr[0] = high[0] - low[0]
        atr = np.zeros_like(tr)
        atr[0] = tr[0]
        for i in range(1, len(tr)):
            atr[i] = (atr[i-1] * (n-1) + tr[i]) / n
        # 振幅与ATR的比率
        amp_ratio = tr / (atr + 1e-8)
        # 异常扩张检测：比率超过1.5
        abnormal = np.where(amp_ratio > 1.5, amp_ratio - 1.5, 0)
        # 累计冲击
        decay = 0.7
        shock = np.zeros_like(tr)
        for i in range(1, len(tr)):
            shock[i] = decay * shock[i-1] + abnormal[i]
        # 输出负相关，波动扩张 -> 风险高，因子值为负
        raw = -shock / (np.max(np.abs(shock)) + 1e-8)
        raw = np.clip(raw, -1, 1)
        result = pd.Series(raw, index=data.index).fillna(0)
        return result
