"""AI因子: 混乱指数 | 置信:55% | 通过ATR与ADX的比值衡量市场无序波动程度，当波动率较高但趋势强度较低时，市场处于未知状态，该因子为负值提示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ChaosIndex(BaseFactor):
    """通过ATR与ADX的比值衡量市场无序波动程度，当波动率较高但趋势强度较低时，市场处于未知状态，该因子为负值提示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_chaos",
            name="Chaos Index",
            display_name="混乱指数",
            description="通过ATR与ADX的比值衡量市场无序波动程度，当波动率较高但趋势强度较低时，市场处于未知状态，该因子为负值提示风险。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR (14)
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 计算ADX (14)
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        tr_smooth = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / tr_smooth
        minus_di = 100 * minus_dm.rolling(14).mean() / tr_smooth
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 混乱指数 = 标准化后的ATR/ADX比值，然后映射到[-1,1]
        ratio = atr / (adx + 1e-10)
        # 使用滚动z-score或分位数映射
        mean_ratio = ratio.rolling(30).mean()
        std_ratio = ratio.rolling(30).std() + 1e-10
        z = (ratio - mean_ratio) / std_ratio
        result = np.clip(z / 3, -1, 1)  # 3倍标准差截断
        return pd.Series(result * -1, index=data.index)  # 取负：混乱越高越危险
