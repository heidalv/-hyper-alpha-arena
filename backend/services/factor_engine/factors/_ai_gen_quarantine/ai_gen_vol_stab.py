"""AI因子: 波动率稳定性指数 | 置信:60% | 衡量近期波动率相对于历史波动率的异常程度。当短期波动率远高于或远低于历史均值时，市场状态不确定性高，此类环境下易出现止损或反转亏损。因子输出负值表示高风险（应避免开仓），正值表示正常状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStabilityIndex(BaseFactor):
    """衡量近期波动率相对于历史波动率的异常程度。当短期波动率远高于或远低于历史均值时，市场状态不确定性高，此类环境下易出现止损或反转亏损。因子输出负值表示高风险（应避免开仓），正值表示正常状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_stab",
            name="Volatility Stability Index",
            display_name="波动率稳定性指数",
            description="衡量近期波动率相对于历史波动率的异常程度。当短期波动率远高于或远低于历史均值时，市场状态不确定性高，此类环境下易出现止损或反转亏损。因子输出负值表示高风险（应避免开仓），正值表示正常状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20日收益率标准差
        returns = data['close'].pct_change()
        hist_vol = returns.rolling(50).std() * np.sqrt(252)  # 年化波动率
        short_vol = returns.rolling(10).std() * np.sqrt(252)
        # 波动率偏离度：短期/长期 - 1，然后归一化到[-1,1]
        ratio = short_vol / hist_vol - 1
        # 使用tanh限制范围，取负值表示异常
        result = -np.tanh(np.abs(ratio) * 5) * np.sign(ratio + 1e-10)
        # 处理缺失值
        result = result.fillna(0).clip(-1, 1)
        return result
