"""AI因子: 波动率崩溃因子 | 置信:60% | 捕捉价格波动率突然收缩或异常放大，暗示市场状态切换。当短期波动率较长期波动率大幅偏离时，市场处于不确定状态，regime未知。返回-1表示高风险波动异常，+1表示波动正常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityBreakdown(BaseFactor):
    """捕捉价格波动率突然收缩或异常放大，暗示市场状态切换。当短期波动率较长期波动率大幅偏离时，市场处于不确定状态，regime未知。返回-1表示高风险波动异常，+1表示波动正常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakdown",
            name="VolatilityBreakdown",
            display_name="波动率崩溃因子",
            description="捕捉价格波动率突然收缩或异常放大，暗示市场状态切换。当短期波动率较长期波动率大幅偏离时，市场处于不确定状态，regime未知。返回-1表示高风险波动异常，+1表示波动正常。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算对数收益率
        log_ret = np.log(close / close.shift(1))
        # 短期波动率（5日标准差）
        short_vol = log_ret.rolling(window=5).std()
        # 长期波动率（20日标准差）
        long_vol = log_ret.rolling(window=20).std()
        # 波动率比值
        vol_ratio = short_vol / (long_vol + 1e-10)
        # 波动率水平绝对值（防止极端值）
        vol_level = long_vol * np.sqrt(252)  # 年化
        # 定义异常条件：比值超出[0.5, 2]范围 或 波动率水平突然飙升（大于历史95分位）
        hist_95 = long_vol.rolling(window=100).quantile(0.95)
        condition = (vol_ratio < 0.5) | (vol_ratio > 2.0) | (long_vol > hist_95)
        # 返回信号：-1表示异常，+1表示正常
        signal = np.where(condition, -1.0, 1.0)
        return pd.Series(signal, index=data.index)
