"""AI因子: 动态止损距离合理性 | 置信:60% | 评估当前价格距关键支撑/阻力的相对位置，结合ATR判断止损距离是否过紧（易被扫损）。若止损过浅则因子偏负，合理则偏正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Adaptive_Stop_Distance_Rationality(BaseFactor):
    """评估当前价格距关键支撑/阻力的相对位置，结合ATR判断止损距离是否过紧（易被扫损）。若止损过浅则因子偏负，合理则偏正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stopdist",
            name="Adaptive Stop Distance Rationality",
            display_name="动态止损距离合理性",
            description="评估当前价格距关键支撑/阻力的相对位置，结合ATR判断止损距离是否过紧（易被扫损）。若止损过浅则因子偏负，合理则偏正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # ATR
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # 近期波动范围
        recent_high = high.rolling(20).max()
        recent_low = low.rolling(20).min()
        price_range = recent_high - recent_low

        # 当前价格在区间内的位置 (0~1)
        pos = (close - recent_low) / (price_range + 1e-10)
        # 过度接近区间边界时止损可能过紧
        edge_dist = np.minimum(pos, 1 - pos)  # 0表示在边界
        # 标准化：用ATR衡量距离是否合理
        dist_in_atr = edge_dist * price_range / (atr + 1e-10)
        # 当距离过小（<1.5 ATR）且波动率未放大时，认为止损过紧->负值
        too_tight = (dist_in_atr < 1.5).astype(float)
        # 同时结合波动率变化：若波动率扩大，过紧风险更大
        atr_pct = atr.pct_change(5)
        risk = too_tight * (1 + atr_pct.clip(0, 1))
        result = 1 - 2 * risk.clip(0, 1)  # 映射到[-1,1]
        return result.fillna(0)
