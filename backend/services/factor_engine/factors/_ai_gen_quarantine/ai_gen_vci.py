"""AI因子: 波动率收缩指数 | 置信:65% | 检测近期波动率是否处于历史低位且价格窄幅震荡，用于识别高概率震荡行情，避免趋势策略在不确定环境中频繁止损。计算ATR与过去20日ATR均值的比值，再结合价格区间宽度，输出-1（强烈震荡/未知状态）到+1（强趋势）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionIndex(BaseFactor):
    """检测近期波动率是否处于历史低位且价格窄幅震荡，用于识别高概率震荡行情，避免趋势策略在不确定环境中频繁止损。计算ATR与过去20日ATR均值的比值，再结合价格区间宽度，输出-1（强烈震荡/未知状态）到+1（强趋势）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vci",
            name="Volatility Contraction Index",
            display_name="波动率收缩指数",
            description="检测近期波动率是否处于历史低位且价格窄幅震荡，用于识别高概率震荡行情，避免趋势策略在不确定环境中频繁止损。计算ATR与过去20日ATR均值的比值，再结合价格区间宽度，输出-1（强烈震荡/未知状态）到+1（强趋势）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        atr_ma = atr.rolling(20).mean()
        atr_ratio = atr / atr_ma
        # 价格区间宽度：近10日高低差与当前ATR比
        recent_high = high.rolling(10).max()
        recent_low = low.rolling(10).min()
        range_width = (recent_high - recent_low) / close
        atr_norm = atr / close
        # 综合得分：当atr比率<0.8且宽度<0.05时强烈震荡信号-1，否则根据相对强度映射到-1~1
        raw = -2 * (1 - atr_ratio.clip(0.5, 1.5)) + range_width * 10
        result = np.tanh(raw - 1)
        return result.fillna(0).clip(-1, 1)
