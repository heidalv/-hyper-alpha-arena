"""AI因子: 日内噪声比率 | 置信:55% | 衡量日内价格噪声程度：计算每根K线的(high-low)/|close-open|比率，若比率很大且价格波动小说明噪声高。噪声高时市场方向不明，适合反趋势或离场。因子值在噪声高时为负，低时为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class IntradayNoiseRatio(BaseFactor):
    """衡量日内价格噪声程度：计算每根K线的(high-low)/|close-open|比率，若比率很大且价格波动小说明噪声高。噪声高时市场方向不明，适合反趋势或离场。因子值在噪声高时为负，低时为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_ratio",
            name="Intraday Noise Ratio",
            display_name="日内噪声比率",
            description="衡量日内价格噪声程度：计算每根K线的(high-low)/|close-open|比率，若比率很大且价格波动小说明噪声高。噪声高时市场方向不明，适合反趋势或离场。因子值在噪声高时为负，低时为正。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        # 防止除以0
        denom = np.abs(close - open_)
        denom = denom.replace(0, np.nan)
        noise = (high - low) / denom
        noise = noise.replace([np.inf, -np.inf], np.nan).fillna(10)  # 处理极端值
        # 取5日均值，阈值3以上视为高噪声
        noise_ma = noise.rolling(5).mean()
        signal = np.where(noise_ma > 3, -0.7, 0.3)
        result = pd.Series(signal, index=close.index).fillna(0)
        return result.clip(-1, 1)
