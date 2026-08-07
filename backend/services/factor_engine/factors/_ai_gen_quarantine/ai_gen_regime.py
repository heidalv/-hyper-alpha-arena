"""AI因子: 市场状态强度 | 置信:65% | 基于价格相对长期均线的偏离度与布林带宽缩放的标准化指标，正值表示强势多头趋势，负值表示强势空头趋势，接近0表示震荡或无方向。用于过滤‘未知’状态下的交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Marketregimestrength(BaseFactor):
    """基于价格相对长期均线的偏离度与布林带宽缩放的标准化指标，正值表示强势多头趋势，负值表示强势空头趋势，接近0表示震荡或无方向。用于过滤‘未知’状态下的交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime",
            name="MarketRegimeStrength",
            display_name="市场状态强度",
            description="基于价格相对长期均线的偏离度与布林带宽缩放的标准化指标，正值表示强势多头趋势，负值表示强势空头趋势，接近0表示震荡或无方向。用于过滤‘未知’状态下的交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20日均线
        ma20 = close.rolling(20).mean()
        # 计算20日布林带宽 (标准差) 并标准化
        std20 = close.rolling(20).std()
        boll_width = 2 * std20 / ma20.replace(0, np.nan)
        # 价格偏离度
        deviation = (close - ma20) / (ma20.replace(0, np.nan))
        # 用带宽缩放偏离度，避免窄带放大噪声
        scaled = deviation / (boll_width + 0.01)
        # 截断到[-1,1]
        result = np.clip(scaled, -1, 1)
        return result
