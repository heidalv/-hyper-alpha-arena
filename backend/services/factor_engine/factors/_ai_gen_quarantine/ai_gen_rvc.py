"""AI因子: 相对波动率变化 | 置信:65% | 计算近期波动率相对于历史波动率的变化率，用于识别市场状态突变（未知状态）。当波动率急剧上升或下降时，市场可能进入不稳定的未知状态，容易导致止损或超时平仓。因子值映射到[-1,1]，正值表示波动率升高，负值表示降低。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RelativeVolatilityChange(BaseFactor):
    """计算近期波动率相对于历史波动率的变化率，用于识别市场状态突变（未知状态）。当波动率急剧上升或下降时，市场可能进入不稳定的未知状态，容易导致止损或超时平仓。因子值映射到[-1,1]，正值表示波动率升高，负值表示降低。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvc",
            name="Relative Volatility Change",
            display_name="相对波动率变化",
            description="计算近期波动率相对于历史波动率的变化率，用于识别市场状态突变（未知状态）。当波动率急剧上升或下降时，市场可能进入不稳定的未知状态，容易导致止损或超时平仓。因子值映射到[-1,1]，正值表示波动率升高，负值表示降低。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算日内波动率：最高最低价的对数差
        log_hl = np.log(data['high'] / data['low'])
        # 滚动窗口20期计算波动率均值
        window = 20
        vol_ma = log_hl.rolling(window=window, min_periods=1).mean()
        vol_std = log_hl.rolling(window=window, min_periods=1).std()
        # 用当前波动率与均值的偏离程度（Z-score）
        zscore = (log_hl - vol_ma) / (vol_std + 1e-10)
        # 截断到[-3,3]再映射到[-1,1]
        clamped = np.clip(zscore, -3, 3)
        return pd.Series(clamped / 3, index=data.index)
