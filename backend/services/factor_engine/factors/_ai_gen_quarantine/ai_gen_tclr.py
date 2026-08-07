"""AI因子: 趋势清晰度 | 置信:70% | 通过比较短期和长期移动平均线的发散程度来衡量趋势的清晰度。当趋势模糊（均线纠缠）时输出接近0，避免在未知市场状态下交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendClarity(BaseFactor):
    """通过比较短期和长期移动平均线的发散程度来衡量趋势的清晰度。当趋势模糊（均线纠缠）时输出接近0，避免在未知市场状态下交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tclr",
            name="TrendClarity",
            display_name="趋势清晰度",
            description="通过比较短期和长期移动平均线的发散程度来衡量趋势的清晰度。当趋势模糊（均线纠缠）时输出接近0，避免在未知市场状态下交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        short_ma = close.rolling(10).mean()
        long_ma = close.rolling(30).mean()
        diff = (short_ma - long_ma) / close
        # 使用滚动标准差衡量发散稳定性
        diff_std = diff.rolling(10).std()
        # 当diff_std很小时说明均线纠缠，信号趋于0；否则按照diff方向给出+/-1
        clarity = np.where(diff_std < 0.001, 0, diff.abs() / (diff_std + 1e-8))
        clarity = np.clip(clarity, 0, 1)
        result = np.sign(diff) * clarity
        return pd.Series(result, index=data.index).fillna(0)
