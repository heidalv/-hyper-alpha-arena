"""AI因子: 短期偏度反转因子 | 置信:60% | 计算最近10日收益率分布的偏度，当偏度绝对值过高时（>1或<-1）预示极端行情后的反转概率增加。正偏度（右尾长）对应多头过度，预期回落，因子值为负；负偏度对应空头过度，预期反弹，因子值为正。用于捕捉regime=unknown下的反转机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTermSkewReversalFactor(BaseFactor):
    """计算最近10日收益率分布的偏度，当偏度绝对值过高时（>1或<-1）预示极端行情后的反转概率增加。正偏度（右尾长）对应多头过度，预期回落，因子值为负；负偏度对应空头过度，预期反弹，因子值为正。用于捕捉regime=unknown下的反转机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_skewrev",
            name="Short-term Skew Reversal Factor",
            display_name="短期偏度反转因子",
            description="计算最近10日收益率分布的偏度，当偏度绝对值过高时（>1或<-1）预示极端行情后的反转概率增加。正偏度（右尾长）对应多头过度，预期回落，因子值为负；负偏度对应空头过度，预期反弹，因子值为正。用于捕捉regime=unknown下的反转机会。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change().fillna(0)
        # 滚动10日偏度，使用scipy或手动计算
        def skewness(x):
            n = len(x)
            if n < 3:
                return 0.0
            mean = np.mean(x)
            std = np.std(x, ddof=0)
            if std == 0:
                return 0.0
            return np.mean((x - mean)**3) / (std**3)
        skew = ret.rolling(10).apply(skewness, raw=True).fillna(0)
        # 极端偏度映射：绝对值>1时激活，方向相反
        factor = -np.sign(skew) * (np.abs(skew).clip(0, 2) / 2.0)
        factor = factor.clip(-1, 1)
        return factor
