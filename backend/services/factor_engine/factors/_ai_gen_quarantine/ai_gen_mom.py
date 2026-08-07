"""AI因子: 标准化动量 | 置信:70% | 计算过去20个交易日的收益率除以同期波动率（标准差），衡量趋势强度。正值表示强势上涨，负值表示强势下跌，接近0表示无明显趋势。值域通过截断映射到[-1,1]。适用于识别趋势明确与震荡区间，可规避趋势不明时的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NormalizedMomentum(BaseFactor):
    """计算过去20个交易日的收益率除以同期波动率（标准差），衡量趋势强度。正值表示强势上涨，负值表示强势下跌，接近0表示无明显趋势。值域通过截断映射到[-1,1]。适用于识别趋势明确与震荡区间，可规避趋势不明时的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom",
            name="Normalized Momentum",
            display_name="标准化动量",
            description="计算过去20个交易日的收益率除以同期波动率（标准差），衡量趋势强度。正值表示强势上涨，负值表示强势下跌，接近0表示无明显趋势。值域通过截断映射到[-1,1]。适用于识别趋势明确与震荡区间，可规避趋势不明时的亏损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 对数收益率
        log_returns = np.log(data['close'] / data['close'].shift(1))
        ret_20 = np.log(data['close'] / data['close'].shift(20))
        vol_20 = log_returns.rolling(20).std() * np.sqrt(20)  # 年化? 但只需相对
        # 防止除零
        vol_20 = vol_20.replace(0, np.nan)
        mom = ret_20 / vol_20
        # 截断到[-1,1]
        mom = mom.clip(-1, 1)
        return mom.fillna(0)
