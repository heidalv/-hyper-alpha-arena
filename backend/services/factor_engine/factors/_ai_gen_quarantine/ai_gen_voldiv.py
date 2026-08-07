"""AI因子: 波动率成交量背离因子 | 置信:60% | 衡量价格波动率与成交量的背离程度。当波动率上升但成交量下降时，表明价格变动缺乏市场共识，容易反转。计算近期ATR与成交量均值的比率的变化率，取负值归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilityvolumedivergence(BaseFactor):
    """衡量价格波动率与成交量的背离程度。当波动率上升但成交量下降时，表明价格变动缺乏市场共识，容易反转。计算近期ATR与成交量均值的比率的变化率，取负值归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voldiv",
            name="VolatilityVolumeDivergence",
            display_name="波动率成交量背离因子",
            description="衡量价格波动率与成交量的背离程度。当波动率上升但成交量下降时，表明价格变动缺乏市场共识，容易反转。计算近期ATR与成交量均值的比率的变化率，取负值归一化到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR(14)
        high, low, close = data['high'], data['low'], data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算成交量均值(14)
        vol = data['volume']
        vol_ma = vol.rolling(14).mean()
        # 计算ATR与成交量比率的短期变化率(5周期)
        ratio = atr / vol_ma.replace(0, np.nan)
        ratio_change = ratio - ratio.shift(5)
        # 标准化到[-1,1]（使用截断和缩放）
        std = ratio_change.std()
        if std == 0 or np.isnan(std):
            return pd.Series(0, index=data.index)
        normalized = -ratio_change / (3 * std)  # 取负使得背离时为正信号
        result = normalized.clip(-1, 1)
        return result.fillna(0)
