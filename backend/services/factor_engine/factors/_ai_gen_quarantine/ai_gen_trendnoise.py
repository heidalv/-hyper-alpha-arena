"""AI因子: 趋势噪音比因子 | 置信:65% | 计算过去N周期收盘价方向变动的一致性，用正收益比例与负收益比例的差值除以总波动率。高正值表示趋势强且一致，适合顺势；负值表示噪音大、方向混乱，容易导致止损。输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendnoiseratio(BaseFactor):
    """计算过去N周期收盘价方向变动的一致性，用正收益比例与负收益比例的差值除以总波动率。高正值表示趋势强且一致，适合顺势；负值表示噪音大、方向混乱，容易导致止损。输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendnoise",
            name="TrendNoiseRatio",
            display_name="趋势噪音比因子",
            description="计算过去N周期收盘价方向变动的一致性，用正收益比例与负收益比例的差值除以总波动率。高正值表示趋势强且一致，适合顺势；负值表示噪音大、方向混乱，容易导致止损。输出[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ret = close.pct_change()
        n = 20
        # 正收益比例
        pos = (ret > 0).rolling(n).sum() / n
        # 负收益比例
        neg = (ret < 0).rolling(n).sum() / n
        # 方向强度
        direction = pos - neg  # -1到1
        # 波动率调节：用平均真实波幅归一化
        high = data['high']
        low = data['low']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 用价格归一化
        norm = atr / close.shift(1)
        # 如果波动率极大，降低信心
        factor = direction * np.clip(1 - norm * 10, 0, 1)
        result = np.clip(factor, -1, 1)
        return pd.Series(result, index=data.index)
