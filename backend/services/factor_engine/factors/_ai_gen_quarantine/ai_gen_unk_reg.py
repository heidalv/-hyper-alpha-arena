"""AI因子: 未知状态风险因子 | 置信:65% | 基于ADX和ATR，当ADX低于阈值（20）且ATR相对价格比值较高（>1.5倍滚动均值）时，市场处于无趋势高波动状态，做多易亏损，输出负值；反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Risk(BaseFactor):
    """基于ADX和ATR，当ADX低于阈值（20）且ATR相对价格比值较高（>1.5倍滚动均值）时，市场处于无趋势高波动状态，做多易亏损，输出负值；反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_reg",
            name="Unknown Regime Risk",
            display_name="未知状态风险因子",
            description="基于ADX和ATR，当ADX低于阈值（20）且ATR相对价格比值较高（>1.5倍滚动均值）时，市场处于无趋势高波动状态，做多易亏损，输出负值；反之输出正值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ADX
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_dm = ((high - high.shift(1)) > (low.shift(1) - low)) * (high - high.shift(1)).clip(lower=0)
        minus_dm = ((low.shift(1) - low) > (high - high.shift(1))) * (low.shift(1) - low).clip(lower=0)
        di_plus = 100 * (plus_dm.rolling(period).mean() / atr)
        di_minus = 100 * (minus_dm.rolling(period).mean() / atr)
        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(period).mean()
        # 计算ATR相对价格比值
        atr_ratio = atr / close
        atr_ratio_ma = atr_ratio.rolling(20).mean()
        # 条件: ADX<20 且 atr_ratio > 1.5 * atr_ratio_ma
        condition = (adx < 20) & (atr_ratio > 1.5 * atr_ratio_ma)
        result = pd.Series(np.where(condition, -1.0, 1.0), index=data.index)
        return result
