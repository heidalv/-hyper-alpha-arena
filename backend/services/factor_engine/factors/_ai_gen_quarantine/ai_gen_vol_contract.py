"""AI因子: 波动率收缩 | 置信:70% | 衡量短期波动率相对于长期波动率的收缩程度。当短期ATR低于长期ATR的某个比例时，表明市场进入低波动区间，容易产生虚假突破和持仓超时亏损。因子值在波动收缩时趋向-1，扩张时趋向1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Contraction(BaseFactor):
    """衡量短期波动率相对于长期波动率的收缩程度。当短期ATR低于长期ATR的某个比例时，表明市场进入低波动区间，容易产生虚假突破和持仓超时亏损。因子值在波动收缩时趋向-1，扩张时趋向1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_contract",
            name="Volatility Contraction",
            display_name="波动率收缩",
            description="衡量短期波动率相对于长期波动率的收缩程度。当短期ATR低于长期ATR的某个比例时，表明市场进入低波动区间，容易产生虚假突破和持仓超时亏损。因子值在波动收缩时趋向-1，扩张时趋向1。",
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
        # 计算ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr_short = tr.rolling(window=10).mean()
        atr_long = tr.rolling(window=50).mean()
        ratio = atr_short / (atr_long + 1e-10)
        # 将ratio映射到[-1,1]，通常ratio在0.5~1.5之间，中心化并压缩
        result = 2 * (ratio - 1)  # 当ratio=1时值为0，小于1负，大于1正
        result = np.clip(result, -1, 1)
        return result
