"""AI因子: 趋势置信度 | 置信:55% | 基于价格动量与波动率比值的趋势强度指标。当价格突破近期均线且波动率适中时置信度高，避免在低波动或无序震荡中交易。计算收盘价相对于20周期均线的偏离度，除以近期波动率（ATR/价格），再经过tanh压缩至[-1,1]。正值表示上升趋势置信度，负值表示下降趋势置信度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConfidenceScore(BaseFactor):
    """基于价格动量与波动率比值的趋势强度指标。当价格突破近期均线且波动率适中时置信度高，避免在低波动或无序震荡中交易。计算收盘价相对于20周期均线的偏离度，除以近期波动率（ATR/价格），再经过tanh压缩至[-1,1]。正值表示上升趋势置信度，负值表示下降趋势置信度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_conf",
            name="Trend Confidence Score",
            display_name="趋势置信度",
            description="基于价格动量与波动率比值的趋势强度指标。当价格突破近期均线且波动率适中时置信度高，避免在低波动或无序震荡中交易。计算收盘价相对于20周期均线的偏离度，除以近期波动率（ATR/价格），再经过tanh压缩至[-1,1]。正值表示上升趋势置信度，负值表示下降趋势置信度。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        # 参数
        period = 20

        # 计算均线
        sma = data['close'].rolling(period).mean()
        # 价格偏离
        deviation = (data['close'] - sma) / sma

        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 波动率标准化
        vol_ratio = atr / data['close']
        vol_ratio = vol_ratio.replace(0, np.nan)  # 避免除零

        # 趋势置信度：偏离度除以波动率（低波动放大偏离，高波动抑制）
        confidence = deviation / vol_ratio
        # 截断并压缩到[-1,1]
        confidence = confidence.clip(-3, 3)
        result = np.tanh(confidence)

        # 处理NaN和无穷
        result = result.fillna(0)
        return result
