"""AI因子: 微波动率因子 | 置信:65% | 捕捉价格微小波动与真实波幅的比率，当价格变动极小但真实波幅也极小时，市场处于窄幅震荡，容易触发微小止损单。使用最近N根K线的平均价格变动幅度除以平均真实波幅(ATR)，并进行z-score归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Micro Volatility Ratio(BaseFactor):
    """捕捉价格微小波动与真实波幅的比率，当价格变动极小但真实波幅也极小时，市场处于窄幅震荡，容易触发微小止损单。使用最近N根K线的平均价格变动幅度除以平均真实波幅(ATR)，并进行z-score归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_vol",
            name="Micro Volatility Ratio",
            display_name="微波动率因子",
            description="捕捉价格微小波动与真实波幅的比率，当价格变动极小但真实波幅也极小时，市场处于窄幅震荡，容易触发微小止损单。使用最近N根K线的平均价格变动幅度除以平均真实波幅(ATR)，并进行z-score归一化到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            n = 20
            close = data['close']
            high = data['high']
            low = data['low']
            # 价格变动绝对值
            price_change = close.diff().abs()
            # 真实波幅
            tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
            # 平滑
            avg_change = price_change.rolling(n, min_periods=n).mean()
            avg_tr = tr.rolling(n, min_periods=n).mean()
            ratio = avg_change / (avg_tr + 1e-10)
            # z-score, 然后clip到[-1,1]
            z = (ratio - ratio.mean()) / ratio.std()
            result = z.clip(-3, 3) / 3.0
            return result.fillna(0)
