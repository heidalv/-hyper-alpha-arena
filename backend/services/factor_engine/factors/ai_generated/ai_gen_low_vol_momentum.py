"""AI因子: 低波动动量陷阱 | 置信:60% | 在低波动环境下，动量容易失效导致假突破亏损。该因子计算近期ATR与价格变化率的比值，并结合价格与20日均线的偏离度，当波动率低且动量弱时输出负值，表示高风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityMomentumTrap(BaseFactor):
    """在低波动环境下，动量容易失效导致假突破亏损。该因子计算近期ATR与价格变化率的比值，并结合价格与20日均线的偏离度，当波动率低且动量弱时输出负值，表示高风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_low_vol_momentum",
            name="Low Volatility Momentum Trap",
            display_name="低波动动量陷阱",
            description="在低波动环境下，动量容易失效导致假突破亏损。该因子计算近期ATR与价格变化率的比值，并结合价格与20日均线的偏离度，当波动率低且动量弱时输出负值，表示高风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算ATR(14)归一化
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 价格变化率(20日)
        ret_20 = close.pct_change(20)
        # 价格与20日均线偏离
        ma20 = close.rolling(20).mean()
        deviation = (close - ma20) / ma20
        # 综合因子：低波动+低动量+小偏离 => 风险区域
        # 标准化ATR (滚动zscore)
        atr_ma = atr.rolling(50).mean()
        atr_std = atr.rolling(50).std()
        atr_z = (atr - atr_ma) / (atr_std + 1e-10)
        # 动量强度(绝对值)
        mom_abs = ret_20.abs()
        # 偏离绝对值
        dev_abs = deviation.abs()
        # 危险评分: 当atr_z低、mom_abs低、dev_abs低时高
        risk = - (atr_z.clip(-3,3) + mom_abs.clip(0,0.5)/0.05 + dev_abs.clip(0,0.2)/0.02) / 3
        result = risk.clip(-1, 1)
        return result
