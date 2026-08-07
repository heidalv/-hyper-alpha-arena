"""AI因子: 波动率反转 | 置信:60% | 利用短期波动率突变识别反转信号。基于ATR比率与价格变化率，当价格在低波动环境中突然出现高波动变化时，往往预示短期反转。因子通过比较当前ATR与历史ATR，并结合价格方向，输出反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilityregimereversal(BaseFactor):
    """利用短期波动率突变识别反转信号。基于ATR比率与价格变化率，当价格在低波动环境中突然出现高波动变化时，往往预示短期反转。因子通过比较当前ATR与历史ATR，并结合价格方向，输出反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_reversal",
            name="VolatilityRegimeReversal",
            display_name="波动率反转",
            description="利用短期波动率突变识别反转信号。基于ATR比率与价格变化率，当价格在低波动环境中突然出现高波动变化时，往往预示短期反转。因子通过比较当前ATR与历史ATR，并结合价格方向，输出反向信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # ATR计算
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        # 波动率比率
        vol_ratio = atr_short / atr_long - 1
        # 价格变化率
        price_change = data['close'].pct_change(3)
        # 信号：当波动率突然放大且价格变动方向与之前趋势相反时
        factor = -np.sign(price_change) * vol_ratio
        factor = factor.fillna(0)
        factor = np.clip(factor, -1, 1)
        return factor
