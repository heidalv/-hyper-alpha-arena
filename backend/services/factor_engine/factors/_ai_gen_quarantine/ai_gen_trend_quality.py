"""AI因子: 趋势质量因子 | 置信:65% | 衡量当前价格相对于中期均线的偏离程度，并用量价关系调整，以识别真实趋势与随机波动的区别。当价格偏离均线且成交量温和放大时，趋势质量高；当价格偏离但成交量萎缩或波动率极低时，趋势质量低，可避免在未知震荡市中追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Quality(BaseFactor):
    """衡量当前价格相对于中期均线的偏离程度，并用量价关系调整，以识别真实趋势与随机波动的区别。当价格偏离均线且成交量温和放大时，趋势质量高；当价格偏离但成交量萎缩或波动率极低时，趋势质量低，可避免在未知震荡市中追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_quality",
            name="Trend Quality",
            display_name="趋势质量因子",
            description="衡量当前价格相对于中期均线的偏离程度，并用量价关系调整，以识别真实趋势与随机波动的区别。当价格偏离均线且成交量温和放大时，趋势质量高；当价格偏离但成交量萎缩或波动率极低时，趋势质量低，可避免在未知震荡市中追涨杀跌。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        # 计算20周期均线和标准差
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        # 价格偏离度（标准化）
        deviation = (close - ma20) / (std20 + 1e-10)
        # 成交量相对20日均值
        vol_ratio = volume / volume.rolling(20).mean()
        # 趋势质量：偏离度乘以成交量比率的对数，并用tanh压缩到[-1,1]
        trend_quality = deviation * (vol_ratio.clip(0.5, 3).apply(np.log))
        result = np.tanh(trend_quality)
        return result
