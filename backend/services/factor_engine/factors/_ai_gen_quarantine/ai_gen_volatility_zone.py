"""AI因子: 波动率区间指标 | 置信:65% | 基于近期ATR与价格波动范围的关系，衡量当前市场是否处于高波动风险区域。当价格波动范围显著超出平均ATR时，表明市场状态不明、易触发止损，输出正信号（高风险）；反之低波动表示稳定，输出负信号（低风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityZoneIndicator(BaseFactor):
    """基于近期ATR与价格波动范围的关系，衡量当前市场是否处于高波动风险区域。当价格波动范围显著超出平均ATR时，表明市场状态不明、易触发止损，输出正信号（高风险）；反之低波动表示稳定，输出负信号（低风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_zone",
            name="Volatility Zone Indicator",
            display_name="波动率区间指标",
            description="基于近期ATR与价格波动范围的关系，衡量当前市场是否处于高波动风险区域。当价格波动范围显著超出平均ATR时，表明市场状态不明、易触发止损，输出正信号（高风险）；反之低波动表示稳定，输出负信号（低风险）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR(14)
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算价格波动范围（过去14天高低差）
        highest = high.rolling(14).max()
        lowest = low.rolling(14).min()
        price_range = highest - lowest
        # 计算波动率区间比率
        ratio = price_range / (atr + 1e-10)
        # 归一化到[-1,1]
        normalized = (ratio - 1.0) / (2.0 + 1e-10)  # 典型值0.5~3, 映射到[-0.75,0.5]左右
        # 用tanh压缩到[-1,1]
        result = np.tanh(normalized * 2.0)
        return result
