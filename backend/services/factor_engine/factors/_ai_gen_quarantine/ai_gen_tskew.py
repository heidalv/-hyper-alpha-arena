"""AI因子: 尾部风险偏度 | 置信:60% | 基于收盘价在日内高低价区间的相对位置，结合近N周期的幅度，识别价格是否出现极端偏移（如快速拉升后回落），在未知状态下此类模式易引发反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Tail_Risk_Skew(BaseFactor):
    """基于收盘价在日内高低价区间的相对位置，结合近N周期的幅度，识别价格是否出现极端偏移（如快速拉升后回落），在未知状态下此类模式易引发反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tskew",
            name="Tail Risk Skew",
            display_name="尾部风险偏度",
            description="基于收盘价在日内高低价区间的相对位置，结合近N周期的幅度，识别价格是否出现极端偏移（如快速拉升后回落），在未知状态下此类模式易引发反转。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        range_ = high.rolling(20).max() - low.rolling(20).min()
        position = (close - low.rolling(20).min()) / (range_ + 1e-10)
        skew = (position - 0.5) * 2
        # 用近5日累计收益调整方向：若为强正向收益则倾向于反转向下
        ret5 = close.pct_change(5)
        factor = -skew * np.sign(ret5) * (1 + abs(ret5))
        result = np.clip(factor, -1, 1)
        return result
