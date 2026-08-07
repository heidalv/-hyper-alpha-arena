"""AI因子: 成交量收敛风险 | 置信:60% | 考察成交量相对于近期均值的萎缩程度，结合价格窄幅波动。成交量显著萎缩且价格波动小，表明市场流动性不足或观望情绪浓厚，此时开多容易遭遇意外反转或超时，因子值向-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Convergence(BaseFactor):
    """考察成交量相对于近期均值的萎缩程度，结合价格窄幅波动。成交量显著萎缩且价格波动小，表明市场流动性不足或观望情绪浓厚，此时开多容易遭遇意外反转或超时，因子值向-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volconv",
            name="Volume Convergence",
            display_name="成交量收敛风险",
            description="考察成交量相对于近期均值的萎缩程度，结合价格窄幅波动。成交量显著萎缩且价格波动小，表明市场流动性不足或观望情绪浓厚，此时开多容易遭遇意外反转或超时，因子值向-1。",
            category="volume",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        volume = data['volume']
        close = data['close']
        # 成交量相对20日均值的比值
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma20 + 1e-8)
        # 价格波动率（用收盘价变化率表示）
        price_range = (close - close.shift(1)).abs() / close.shift(1)
        # 当成交量萎缩（<0.7）且价格波动小（<0.005）时，风险高
        # 用双曲正切组合
        condition = (vol_ratio < 0.7) & (price_range < 0.005)
        raw = np.where(condition, -0.8, 0.2 * (vol_ratio - 1))
        result = np.tanh(raw)
        result = pd.Series(result, index=data.index).fillna(0)
        return result
