"""AI因子: 趋势混乱度 | 置信:60% | 通过计算收盘价相对于多条指数移动平均线的偏离度标准差，衡量当前市场趋势的混乱程度。值接近+1表示趋势模糊、价格围绕均线反复穿越，适合规避趋势策略；值接近-1表示趋势清晰，适合顺势操作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEntropy(BaseFactor):
    """通过计算收盘价相对于多条指数移动平均线的偏离度标准差，衡量当前市场趋势的混乱程度。值接近+1表示趋势模糊、价格围绕均线反复穿越，适合规避趋势策略；值接近-1表示趋势清晰，适合顺势操作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trentropy",
            name="Trend Entropy",
            display_name="趋势混乱度",
            description="通过计算收盘价相对于多条指数移动平均线的偏离度标准差，衡量当前市场趋势的混乱程度。值接近+1表示趋势模糊、价格围绕均线反复穿越，适合规避趋势策略；值接近-1表示趋势清晰，适合顺势操作。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算多条EMA
        ema3 = data['close'].ewm(span=3, adjust=False).mean()
        ema5 = data['close'].ewm(span=5, adjust=False).mean()
        ema8 = data['close'].ewm(span=8, adjust=False).mean()
        ema13 = data['close'].ewm(span=13, adjust=False).mean()
        ema21 = data['close'].ewm(span=21, adjust=False).mean()
        # 计算偏离度百分比
        def deviation(ma):
            return (data['close'] - ma) / ma
        dev3 = deviation(ema3)
        dev5 = deviation(ema5)
        dev8 = deviation(ema8)
        dev13 = deviation(ema13)
        dev21 = deviation(ema21)
        # 计算偏离度标准差（滚动窗口20天）
        std = pd.concat([dev3, dev5, dev8, dev13, dev21], axis=1).std(axis=1)
        # 使用atanh归一化到[-1,1]范围，并取负（高标准差->正熵值）
        # 先用滚动min-max归一化
        rolling_max = std.rolling(50, min_periods=20).max()
        rolling_min = std.rolling(50, min_periods=20).min()
        normalized = (std - rolling_min) / (rolling_max - rolling_min + 1e-10)
        # 映射到[-1,1]，0.5作为阈值
        result = (normalized - 0.5) * 2.0
        result = result.clip(-1, 1)
        return result
