"""AI因子: 反转波动比 | 置信:50% | 基于近期价格反转概率和波动率放大，识别可能发生趋势反转或剧烈波动的时刻。计算过去N周期内收盘价相对于开盘价的方向变化次数与平均真实波幅的比值，并归一化至[-1,1]。高正值表示强反转信号，低负值表示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalVolatilityRatio(BaseFactor):
    """基于近期价格反转概率和波动率放大，识别可能发生趋势反转或剧烈波动的时刻。计算过去N周期内收盘价相对于开盘价的方向变化次数与平均真实波幅的比值，并归一化至[-1,1]。高正值表示强反转信号，低负值表示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvr",
            name="Reversal Volatility Ratio",
            display_name="反转波动比",
            description="基于近期价格反转概率和波动率放大，识别可能发生趋势反转或剧烈波动的时刻。计算过去N周期内收盘价相对于开盘价的方向变化次数与平均真实波幅的比值，并归一化至[-1,1]。高正值表示强反转信号，低负值表示趋势延续。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        N = 5
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        # 计算每期涨跌方向
        direction = np.sign(close - open_)
        # 过去N期方向变化次数（反转次数）
        reversal_count = (direction.diff() != 0).rolling(N).sum()
        # ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(20).mean()
        # 归一化因子：反转次数/最大可能反转次数(N-1)，乘以价格变化幅度与ATR的比值
        rev_ratio = reversal_count / (N - 1)
        price_range = np.abs(close - open_) / (atr + 1e-8)
        signal = rev_ratio * (price_range - 1)  # 当price_range大且反转多时正信号
        # 缩放到[-1,1]
        result = np.clip(signal / 2, -1, 1)
        return pd.Series(result, index=data.index)
