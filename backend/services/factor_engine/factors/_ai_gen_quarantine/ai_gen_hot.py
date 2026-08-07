"""AI因子: 持有超时风险 | 置信:60% | 衡量价格在一段时间内围绕开仓成本区间窄幅波动的程度，用于评估横盘震荡导致止损或超时的风险。值越接近-1表示横盘概率高，值越接近1表示趋势明显。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutIndicator(BaseFactor):
    """衡量价格在一段时间内围绕开仓成本区间窄幅波动的程度，用于评估横盘震荡导致止损或超时的风险。值越接近-1表示横盘概率高，值越接近1表示趋势明显。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hot",
            name="Hold_Timeout_Indicator",
            display_name="持有超时风险",
            description="衡量价格在一段时间内围绕开仓成本区间窄幅波动的程度，用于评估横盘震荡导致止损或超时的风险。值越接近-1表示横盘概率高，值越接近1表示趋势明显。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 10  # 观察窗口
        # 计算过去period天的价格区间宽度相对于平均波动
        high = data['high'].rolling(period).max()
        low = data['low'].rolling(period).min()
        range_width = high - low
        # 平均真实波幅
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 横盘程度：区间宽度与ATR的比值，越小越横盘
        ratio = range_width / atr.clip(lower=0.001)  # 避免除零
        # 标准化，通常窄幅时ratio接近1，宽幅时>2
        # 映射到[-1,1]，越窄越负
        result = 1 - ratio  # 当ratio=1 => 0; ratio=2 => -1; ratio=0 => 1（但不会为0）
        result = np.clip(result, -1, 1)
        return result
