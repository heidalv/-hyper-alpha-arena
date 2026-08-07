"""AI因子: 波动自适应动量 | 置信:65% | 基于近期波动率调整动量信号，在低波动（未知市场状态）时削弱信号，在高波动时增强趋势跟随。避免在横盘震荡中盲目开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolAdaptiveMomentum(BaseFactor):
    """基于近期波动率调整动量信号，在低波动（未知市场状态）时削弱信号，在高波动时增强趋势跟随。避免在横盘震荡中盲目开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volad",
            name="VolAdaptiveMomentum",
            display_name="波动自适应动量",
            description="基于近期波动率调整动量信号，在低波动（未知市场状态）时削弱信号，在高波动时增强趋势跟随。避免在横盘震荡中盲目开仓。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR作为波动率
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        norm_atr = atr / close.rolling(14).mean()  # 相对波动率
        # 动量：20日收益
        ret = close.pct_change(20)
        # 波动率调整系数：当相对波动率低于历史20%分位数时视为低波动
        vol_percentile = norm_atr.rolling(50).quantile(0.2)
        vol_factor = np.where(norm_atr < vol_percentile, 0.0, 1.0)
        # 平滑动量信号
        result = np.clip(ret * vol_factor * 5, -1, 1)
        return pd.Series(result, index=data.index).fillna(0)
