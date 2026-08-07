"""AI因子: 价格停滞反转信号 | 置信:60% | 检测价格在窄区间内长时间盘整后出现的突破失败或反转倾向，这种模式常导致超时或反转止损。利用过去N根K线的真实波幅中位值，如果近期波幅显著低于中位值，则输出负值预示风险；否则正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceStallContrarian(BaseFactor):
    """检测价格在窄区间内长时间盘整后出现的突破失败或反转倾向，这种模式常导致超时或反转止损。利用过去N根K线的真实波幅中位值，如果近期波幅显著低于中位值，则输出负值预示风险；否则正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pricestall",
            name="Price_Stall_Contrarian",
            display_name="价格停滞反转信号",
            description="检测价格在窄区间内长时间盘整后出现的突破失败或反转倾向，这种模式常导致超时或反转止损。利用过去N根K线的真实波幅中位值，如果近期波幅显著低于中位值，则输出负值预示风险；否则正。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high, low = data['high'], data['low']
        tr = high - low  # 简化真实波幅
        median_tr = tr.rolling(20).median()
        recent_tr = tr.rolling(3).mean()
        ratio = recent_tr / (median_tr + 1e-10)
        # ratio<0.5为停滞，输出负值；>1.5为活跃输出正值
        score = (ratio - 1) * 2
        result = np.clip(score, -1, 1)
        result[:20] = 0
        return result
