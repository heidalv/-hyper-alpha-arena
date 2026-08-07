"""AI因子: 波动趋势相对位置 | 置信:60% | 基于当前短期波动率相对于长期波动率的位置，识别市场状态是否处于极端低波动（regime unknown）或高波动区域。低波动时因子值接近-1，高波动时接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTrendRelative(BaseFactor):
    """基于当前短期波动率相对于长期波动率的位置，识别市场状态是否处于极端低波动（regime unknown）或高波动区域。低波动时因子值接近-1，高波动时接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltren",
            name="Volatility Trend Relative",
            display_name="波动趋势相对位置",
            description="基于当前短期波动率相对于长期波动率的位置，识别市场状态是否处于极端低波动（regime unknown）或高波动区域。低波动时因子值接近-1，高波动时接近+1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算对数收益率
        ret = np.log(data['close'] / data['close'].shift(1))
        # 短期波动率（20日标准差）
        short_vol = ret.rolling(20).std()
        # 长期波动率（100日标准差）
        long_vol = ret.rolling(100).std()
        # 避免除零
        ratio = short_vol / long_vol.replace(0, np.nan)
        # 计算z-score：相对于历史100日均值和标准差
        mean_ratio = ratio.rolling(100).mean()
        std_ratio = ratio.rolling(100).std()
        z = (ratio - mean_ratio) / std_ratio.replace(0, np.nan)
        # 用tanh映射到[-1,1]
        result = np.tanh(z)
        # 填充前100个NaN为0（中性）
        result = result.fillna(0.0)
        return result
