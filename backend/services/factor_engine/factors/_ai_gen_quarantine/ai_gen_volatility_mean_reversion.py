"""AI因子: 波动率调整均值回复强度 | 置信:55% | 在低波动率环境下，价格偏离历史均值时容易发生均值回复；高波动率时趋势延续概率大。使用20期标准差衡量波动率，当波动率低于其历史50%分位数且偏离20期均线超过1个标准差时，发出反向信号，否则为趋势信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class volatility_adjusted_mean_reversion(BaseFactor):
    """在低波动率环境下，价格偏离历史均值时容易发生均值回复；高波动率时趋势延续概率大。使用20期标准差衡量波动率，当波动率低于其历史50%分位数且偏离20期均线超过1个标准差时，发出反向信号，否则为趋势信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_mean_reversion",
            name="volatility_adjusted_mean_reversion",
            display_name="波动率调整均值回复强度",
            description="在低波动率环境下，价格偏离历史均值时容易发生均值回复；高波动率时趋势延续概率大。使用20期标准差衡量波动率，当波动率低于其历史50%分位数且偏离20期均线超过1个标准差时，发出反向信号，否则为趋势信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        # 波动率水平：滚动60期std20的中位数作为阈值
        vol_med = std20.rolling(60).median().fillna(method='bfill').fillna(std20)
        # 偏离程度：当前价格与均线的距离，用标准差归一化
        zscore = (close - ma20) / std20.replace(0, np.nan).fillna(1)
        # 低波动率条件：当前std20低于vol_med*0.8视为低波动
        low_vol = std20 < vol_med * 0.8
        # 当低波动且偏离超过1.5个标准差时，均值回复信号
        signal = np.where(
            low_vol & (zscore > 1.5), -1.0,
            np.where(low_vol & (zscore < -1.5), 1.0,
                     np.clip(zscore * 0.5, -0.5, 0.5))
        )
        return pd.Series(signal, index=close.index)
