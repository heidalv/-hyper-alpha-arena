"""AI因子: 低波动率风险因子 | 置信:65% | 基于ATR与价格的比率，当波动率处于历史低位时，市场可能处于盘整无方向状态，做多易触发止损。因子负值表示波动率过低，做多风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityRiskIndicator(BaseFactor):
    """基于ATR与价格的比率，当波动率处于历史低位时，市场可能处于盘整无方向状态，做多易触发止损。因子负值表示波动率过低，做多风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_low",
            name="Low Volatility Risk Indicator",
            display_name="低波动率风险因子",
            description="基于ATR与价格的比率，当波动率处于历史低位时，市场可能处于盘整无方向状态，做多易触发止损。因子负值表示波动率过低，做多风险高。",
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
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        vol_ratio = atr / close
        # 计算过去60天的百分位排名（z-score替代）     rolling_mean = vol_ratio.rolling(60).mean()
        rolling_std = vol_ratio.rolling(60).std()
        z = (vol_ratio - rolling_mean) / rolling_std.replace(0, np.nan)
        # 当z为负且绝对值大时表示波动率低，用负z映射到[-1,1]     factor = -z.clip(-3, 3) / 3.0
        factor = factor.fillna(0)
        return factor
