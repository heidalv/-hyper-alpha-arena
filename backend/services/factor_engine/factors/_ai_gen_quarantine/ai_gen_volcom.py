"""AI因子: 波动率压缩因子 | 置信:60% | 基于近期布林带宽度（2倍标准差）相对于历史宽度的变化率，压缩时接近-1，扩张时接近+1。用于识别低波动震荡区间，避免在该区间内频繁突破假信号止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilitycompression(BaseFactor):
    """基于近期布林带宽度（2倍标准差）相对于历史宽度的变化率，压缩时接近-1，扩张时接近+1。用于识别低波动震荡区间，避免在该区间内频繁突破假信号止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volcom",
            name="VolatilityCompression",
            display_name="波动率压缩因子",
            description="基于近期布林带宽度（2倍标准差）相对于历史宽度的变化率，压缩时接近-1，扩张时接近+1。用于识别低波动震荡区间，避免在该区间内频繁突破假信号止损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        import pandas as pd
        close = data['close']
        # 计算20周期均线和标准差
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        # 布林带宽度 = 2 * std / ma (相对宽度)
        bw = (2 * std) / (ma + 1e-10)
        # 计算过去40周期宽度的均值作为基准
        bw_long = bw.rolling(40).mean()
        # 当前宽度相对于历史均值的偏离率
        ratio = (bw - bw_long) / (bw_long + 1e-10)
        # 使用tanh压缩到[-1,1]
        result = np.tanh(ratio * 3)  # 放大敏感度
        result = result.fillna(0).clip(-1, 1)
        return result
