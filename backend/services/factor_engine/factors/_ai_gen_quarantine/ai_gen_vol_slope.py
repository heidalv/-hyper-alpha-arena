"""AI因子: 波动率斜率比 | 置信:60% | 计算短期波动率（5日）与长期波动率（20日）的比值，捕捉市场波动结构变化。当短期波动率快速上升且高于长期波动率时，市场可能进入趋势性行情；反之则可能震荡。正值表示趋势增强，负值表示均值回复倾向。用于识别 regime，避免在未知状态下交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySlopeRatio(BaseFactor):
    """计算短期波动率（5日）与长期波动率（20日）的比值，捕捉市场波动结构变化。当短期波动率快速上升且高于长期波动率时，市场可能进入趋势性行情；反之则可能震荡。正值表示趋势增强，负值表示均值回复倾向。用于识别 regime，避免在未知状态下交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_slope",
            name="Volatility Slope Ratio",
            display_name="波动率斜率比",
            description="计算短期波动率（5日）与长期波动率（20日）的比值，捕捉市场波动结构变化。当短期波动率快速上升且高于长期波动率时，市场可能进入趋势性行情；反之则可能震荡。正值表示趋势增强，负值表示均值回复倾向。用于识别 regime，避免在未知状态下交易。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        returns = close.pct_change()
        vol_short = returns.rolling(5).std()
        vol_long = returns.rolling(20).std()
        # 避免除以零
        vol_long = vol_long.replace(0, np.nan)
        ratio = np.log(vol_short / vol_long)
        # 将极值限制在[-1,1]内
        ratio = ratio.clip(-1, 1)
        return ratio.fillna(0)
