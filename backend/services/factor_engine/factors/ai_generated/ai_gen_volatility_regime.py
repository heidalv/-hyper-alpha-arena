"""AI因子: 波动率状态因子 | 置信:65% | 基于历史波动率与近期波动率的比值，识别市场是否处于异常波动区间。当波动率从低水平突然放大或处于极端高位时，容易触发止损或超时，因子给出负值警告。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeFactor(BaseFactor):
    """基于历史波动率与近期波动率的比值，识别市场是否处于异常波动区间。当波动率从低水平突然放大或处于极端高位时，容易触发止损或超时，因子给出负值警告。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_regime",
            name="Volatility Regime Factor",
            display_name="波动率状态因子",
            description="基于历史波动率与近期波动率的比值，识别市场是否处于异常波动区间。当波动率从低水平突然放大或处于极端高位时，容易触发止损或超时，因子给出负值警告。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算日收益率
        returns = close.pct_change()
        # 历史波动率（60日滚动标准差）
        hist_vol = returns.rolling(60).std()
        # 近期波动率（10日滚动标准差）
        recent_vol = returns.rolling(10).std()
        # 比值，经log变换并压缩
        ratio = (recent_vol / hist_vol) - 1.0  # 偏离程度
        # 当ratio较大（波动率突变）或较小（波动率萎缩）都可能不好，但常见亏损是突变
        # 取正值表示突变，取负值表示萎缩，我们关注的是突变风险
        # 使用tanh限制，符号：突变危险为负，平稳为正
        result = -np.tanh(3 * ratio)
        return result.fillna(0)
