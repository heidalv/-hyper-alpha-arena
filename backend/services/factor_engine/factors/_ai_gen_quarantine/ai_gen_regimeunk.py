"""AI因子: 市场状态不确定性 | 置信:65% | 结合波动率异常、成交量异常和趋势强度缺失来识别'unknown' regime。当传统动量因子失效（价格无明确方向）且波动率骤升、成交量极端放大时，市场处于不确定性状态，预测后续易出现止损或反转。因子输出负值表示高风险未知状态，正值表示稳定状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertainty(BaseFactor):
    """结合波动率异常、成交量异常和趋势强度缺失来识别'unknown' regime。当传统动量因子失效（价格无明确方向）且波动率骤升、成交量极端放大时，市场处于不确定性状态，预测后续易出现止损或反转。因子输出负值表示高风险未知状态，正值表示稳定状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regimeunk",
            name="Regime Uncertainty",
            display_name="市场状态不确定性",
            description="结合波动率异常、成交量异常和趋势强度缺失来识别'unknown' regime。当传统动量因子失效（价格无明确方向）且波动率骤升、成交量极端放大时，市场处于不确定性状态，预测后续易出现止损或反转。因子输出负值表示高风险未知状态，正值表示稳定状态。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        lookback = 20
        # 计算收益率
        returns = data['close'].pct_change()
        # 波动率：过去lookback标准差
        vol = returns.rolling(lookback, min_periods=10).std()
        vol_z = (vol - vol.rolling(lookback*2, min_periods=10).mean()) / vol.rolling(lookback*2, min_periods=10).std()
        # 成交量异常：当前量相对于过去均值的倍数
        vol_ma = data['volume'].rolling(lookback, min_periods=10).mean()
        vol_ratio = data['volume'] / vol_ma
        # 趋势强度：用价格位置判断（远离中位数表示趋势强）
        median_price = data['close'].rolling(lookback, min_periods=10).median()
        price_dev = (data['close'] - median_price) / (data['close'].rolling(lookback, min_periods=10).std() + 1e-10)
        trend_strength = np.abs(price_dev)
        # 不确定性得分：高波动异常 + 高成交量异常 + 低趋势强度
        vol_surge = (vol_z > 1.5).astype(float)
        vol_surge_signal = (vol_ratio > 2.0).astype(float)
        trend_weak = (trend_strength < 0.5).astype(float)
        # 综合：当同时满足高波动、高成交量、弱趋势时为未知状态
        unknown_signal = vol_surge * vol_surge_signal * trend_weak
        # 输出负值表示高风险未知状态
        result = -1.0 * unknown_signal
        # 平滑（可选）
        result = result.rolling(3, min_periods=1).mean()
        return result
