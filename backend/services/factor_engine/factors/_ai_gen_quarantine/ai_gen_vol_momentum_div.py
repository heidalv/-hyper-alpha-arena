"""AI因子: 成交量动量背离 | 置信:55% | 当价格下跌但成交量放大，且近期动量转正，预示空头力量衰竭，避免做空。结合RSI和成交量变化率。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeMomentumDivergence(BaseFactor):
    """当价格下跌但成交量放大，且近期动量转正，预示空头力量衰竭，避免做空。结合RSI和成交量变化率。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_momentum_div",
            name="Volume Momentum Divergence",
            display_name="成交量动量背离",
            description="当价格下跌但成交量放大，且近期动量转正，预示空头力量衰竭，避免做空。结合RSI和成交量变化率。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算价格动量 (3日变化率)
        mom = close.pct_change(3)
        # 计算成交量变化率 (3日平均相对20日平均)
        vol_short = volume.rolling(3).mean()
        vol_long = volume.rolling(20).mean()
        vol_ratio = vol_short / vol_long
        # 价格下跌 (mom<0) 且成交量放大 (vol_ratio>1.2)
        condition = (mom < -0.01) & (vol_ratio > 1.2)
        # 使用RSI判断超卖
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-10)))
        # 超卖且条件满足则做多信号
        signal = np.where(condition & (rsi < 30), 1, 0)
        # 平滑处理
        result = pd.Series(signal * 0.8 - 0.2, index=data.index)  # 偏向避免做空
        return result
