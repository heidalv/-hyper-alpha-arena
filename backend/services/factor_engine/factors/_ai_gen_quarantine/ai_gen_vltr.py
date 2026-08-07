"""AI因子: 波动率状态指示器 | 置信:60% | 比较短期波动率与长期波动率比率，并考虑价格位置。当短期波动率显著高于长期且价格处于区间高位时，认作多头趋势启动（+1）；短期波动率低且价格窄幅震荡则接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeIndicator(BaseFactor):
    """比较短期波动率与长期波动率比率，并考虑价格位置。当短期波动率显著高于长期且价格处于区间高位时，认作多头趋势启动（+1）；短期波动率低且价格窄幅震荡则接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vltr",
            name="Volatility Regime Indicator",
            display_name="波动率状态指示器",
            description="比较短期波动率与长期波动率比率，并考虑价格位置。当短期波动率显著高于长期且价格处于区间高位时，认作多头趋势启动（+1）；短期波动率低且价格窄幅震荡则接近0。",
            category="volatility",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算短期波动率（10周期标准差的年化）
        ret = data['close'].pct_change()
        vol_short = ret.rolling(10).std() * np.sqrt(365)
        # 长期波动率（60周期）
        vol_long = ret.rolling(60).std() * np.sqrt(365)
        # 波动率比率
        vol_ratio = vol_short / (vol_long + 1e-10)
        # 价格相对位置：当前收盘在最近60周期高低点中的位置
        rolling_high = data['high'].rolling(60).max()
        rolling_low = data['low'].rolling(60).min()
        price_position = (data['close'] - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 综合：当vol_ratio > 1.2且price_position > 0.6时，多头信号；当vol_ratio < 0.8且price_position < 0.4时，空头信号
        # 使用sigmoid平滑
        raw_signal = (vol_ratio - 1.0) * (price_position - 0.5) * 4  # 乘积放大
        # 映射到[-1,1]使用tanh
        factor = np.tanh(raw_signal)
        # 填充缺失值为0
        factor = factor.fillna(0)
        return factor.clip(-1, 1)
