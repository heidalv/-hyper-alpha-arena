"""AI因子: 趋势波动比 | 置信:60% | 计算近期价格趋势强度与波动率的比值，用于识别市场状态是否清晰。当趋势弱而波动大时，信号为负（-1），表示高不确定性；趋势强且波动小时，信号为正（+1），表示低不确定性。使用收盘价计算简单移动平均斜率作为趋势强度，ATR作为波动率，标准化后映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendVolatilityRatio(BaseFactor):
    """计算近期价格趋势强度与波动率的比值，用于识别市场状态是否清晰。当趋势弱而波动大时，信号为负（-1），表示高不确定性；趋势强且波动小时，信号为正（+1），表示低不确定性。使用收盘价计算简单移动平均斜率作为趋势强度，ATR作为波动率，标准化后映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tvratio",
            name="Trend_Volatility_Ratio",
            display_name="趋势波动比",
            description="计算近期价格趋势强度与波动率的比值，用于识别市场状态是否清晰。当趋势弱而波动大时，信号为负（-1），表示高不确定性；趋势强且波动小时，信号为正（+1），表示低不确定性。使用收盘价计算简单移动平均斜率作为趋势强度，ATR作为波动率，标准化后映射到[-1,1]。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 趋势强度：20日收盘价线性回归斜率
        window = 20
        def slope(series):
            x = np.arange(window)
            y = series.values
            if len(y) < window:
                return np.nan
            return np.polyfit(x, y, 1)[0]
        trend = close.rolling(window).apply(slope, raw=False)
        # ATR(14)作为波动率
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 避免除零
        ratio = trend / (atr + 1e-9)
        # 标准化到[-1,1]：用滚动z-score或者固定阈值，这里用rolling z-score再tanh
        mean = ratio.rolling(50).mean()
        std = ratio.rolling(50).std()
        z = (ratio - mean) / (std + 1e-9)
        result = np.tanh(z)
        return result.fillna(0).clip(-1, 1)
