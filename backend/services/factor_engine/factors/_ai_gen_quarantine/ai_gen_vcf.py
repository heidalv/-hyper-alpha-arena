"""AI因子: 波幅收缩因子 | 置信:65% | 通过计算近期价格波动范围与成交量变化的比值，识别市场处于低波动收缩状态，此时趋势不明朗，应避免开仓。当波幅收缩且成交量萎缩时，因子值为-1表示危险；当波幅扩张且成交量放大时，因子值为+1表示安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionFactor(BaseFactor):
    """通过计算近期价格波动范围与成交量变化的比值，识别市场处于低波动收缩状态，此时趋势不明朗，应避免开仓。当波幅收缩且成交量萎缩时，因子值为-1表示危险；当波幅扩张且成交量放大时，因子值为+1表示安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcf",
            name="Volatility Contraction Factor",
            display_name="波幅收缩因子",
            description="通过计算近期价格波动范围与成交量变化的比值，识别市场处于低波动收缩状态，此时趋势不明朗，应避免开仓。当波幅收缩且成交量萎缩时，因子值为-1表示危险；当波幅扩张且成交量放大时，因子值为+1表示安全。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        import pandas as pd
        import numpy as np

        # 计算真实波幅ATR（20日）
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()

        # 计算价格相对波动率（20日标准差/均值）
        price_std = close.rolling(20).std()
        price_mean = close.rolling(20).mean()
        cv = price_std / price_mean

        # 成交量移动平均（20日）
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma

        # 构造因子：低波幅+低成交量=>-1，高波幅+高成交量=>+1
        # 标准化ATR和CV
        atr_norm = (atr - atr.rolling(100).mean()) / atr.rolling(100).std()
        cv_norm = (cv - cv.rolling(100).mean()) / cv.rolling(100).std()
        vol_norm = (vol_ratio - 1) / vol_ratio.rolling(100).std()

        # 综合得分（权重可调）
        score = -0.5 * atr_norm - 0.3 * cv_norm + 0.2 * vol_norm
        # 映射到[-1,1]
        score = score.clip(-3, 3) / 3.0
        return score.fillna(0)
