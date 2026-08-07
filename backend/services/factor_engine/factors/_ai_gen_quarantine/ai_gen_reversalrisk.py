"""AI因子: 反转风险因子 | 置信:60% | 基于过去10根K线的上下影线比例和收盘位置，判断价格反转的可能性。当出现长上影线或长下影线且收盘在极端位置时，暗示多空分歧大，因子值接近-1（不利）；连续小实体阳线则因子值接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Risk_Factor(BaseFactor):
    """基于过去10根K线的上下影线比例和收盘位置，判断价格反转的可能性。当出现长上影线或长下影线且收盘在极端位置时，暗示多空分歧大，因子值接近-1（不利）；连续小实体阳线则因子值接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversalrisk",
            name="Reversal Risk Factor",
            display_name="反转风险因子",
            description="基于过去10根K线的上下影线比例和收盘位置，判断价格反转的可能性。当出现长上影线或长下影线且收盘在极端位置时，暗示多空分歧大，因子值接近-1（不利）；连续小实体阳线则因子值接近+1。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        open_p = data['open']
        high = data['high']
        low = data['low']
        close = data['close']

        window = 10
        # 计算上下影线相对长度（相对于价格范围）
        body = abs(close - open_p)
        upper_shadow = high - np.maximum(close, open_p)
        lower_shadow = np.minimum(close, open_p) - low
        total_range = high - low
        # 防止除零
        total_range = np.where(total_range == 0, 1e-6, total_range)
        upper_ratio = upper_shadow / total_range
        lower_ratio = lower_shadow / total_range

        # 多空分歧指标：上影线或下影线比例超过0.6表示分歧大
        divergence = ((upper_ratio > 0.6) | (lower_ratio > 0.6)).astype(float)
        # 收盘在近端（靠近上影线最高点或下影线最低点）-> 不利
        close_near_high = (close >= (high - 0.1 * total_range)).astype(float) * upper_ratio
        close_near_low = (close <= (low + 0.1 * total_range)).astype(float) * lower_ratio

        # 近期平均分歧程度
        div_avg = divergence.rolling(window).mean()
        near_avg = (close_near_high + close_near_low).rolling(window).mean()

        # 合成因子：分歧大且收盘极端 -> 负值；连续小实体 -> 正值
        # 小实体判断：body/total_range < 0.3
        small_body = (body / total_range < 0.3).astype(float)
        small_body_avg = small_body.rolling(window).mean()

        result = (small_body_avg * 0.5) - (div_avg * 0.5 + near_avg * 0.5)
        return pd.Series(np.clip(result, -1, 1), index=data.index).fillna(0.0)
