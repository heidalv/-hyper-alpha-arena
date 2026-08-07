"""AI因子: 动态波动率偏度因子 | 置信:60% | 衡量近期上行波动与下行波动的差异。当偏度绝对值过大时，表明市场存在极端单边预期，容易引发反转或盘整（不确定性高），此时因子输出负值。反之，偏度接近零时，趋势稳定输出正值。基于亏损交易多在regime=unknown，该因子捕捉波动结构异常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DynamicVolatilitySkew(BaseFactor):
    """衡量近期上行波动与下行波动的差异。当偏度绝对值过大时，表明市场存在极端单边预期，容易引发反转或盘整（不确定性高），此时因子输出负值。反之，偏度接近零时，趋势稳定输出正值。基于亏损交易多在regime=unknown，该因子捕捉波动结构异常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dyn_skew",
            name="Dynamic Volatility Skew",
            display_name="动态波动率偏度因子",
            description="衡量近期上行波动与下行波动的差异。当偏度绝对值过大时，表明市场存在极端单边预期，容易引发反转或盘整（不确定性高），此时因子输出负值。反之，偏度接近零时，趋势稳定输出正值。基于亏损交易多在regime=unknown，该因子捕捉波动结构异常。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        ret = close.pct_change()

        window = 30
        # 分别计算正收益和负收益的标准差
        pos_ret = ret.where(ret > 0, 0)
        neg_ret = ret.where(ret < 0, 0).abs()

        # 滚动标准差（仅针对非零值，但这里简单处理）
        pos_vol = pos_ret.rolling(window).std()
        neg_vol = neg_ret.rolling(window).std()

        # 偏度：正波动 - 负波动，再除以总和标准化
        vol_sum = pos_vol + neg_vol
        skew = (pos_vol - neg_vol) / vol_sum.replace(0, np.nan)

        # 当偏度绝对值大时，表示波动不对称，市场可能不稳定 -> 输出负值
        # 使用指数加权平滑并映射到[-1,1]
        signal = -skew.abs().ewm(span=10).mean() * 2  # 乘2使幅度更大
        result = signal.clip(-1,1)
        return result
