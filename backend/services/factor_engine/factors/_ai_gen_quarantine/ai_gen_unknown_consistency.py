"""AI因子: 量价背离度 | 置信:55% | 检测成交量与价格变化方向的不一致性。当价格小幅波动但成交量异常放大，或价格趋势清晰但成交量萎缩，都暗示市场结构不稳定，容易触发止损或超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence(BaseFactor):
    """检测成交量与价格变化方向的不一致性。当价格小幅波动但成交量异常放大，或价格趋势清晰但成交量萎缩，都暗示市场结构不稳定，容易触发止损或超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_consistency",
            name="Volume-Price Divergence",
            display_name="量价背离度",
            description="检测成交量与价格变化方向的不一致性。当价格小幅波动但成交量异常放大，或价格趋势清晰但成交量萎缩，都暗示市场结构不稳定，容易触发止损或超时亏损。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化率
        pct_chg = close.pct_change()
        # 成交量变化率
        vol_chg = volume.pct_change()
        # 计算量价相关系数的滚动值（符号一致性）
        # 使用协方差符号度量
        sign_consistency = np.sign(pct_chg) * np.sign(vol_chg)  # 同向为1，反向为-1，零为0
        # 但我们需要异常情况：价格无变动（pct_chg接近0）但成交量放大 -> 高风险
        near_zero_price = (pct_chg.abs() < 0.002).astype(float)
        high_volume = (vol_chg.abs() > 0.2).astype(float)
        risk1 = near_zero_price * high_volume
        # 或者价格大幅变动但成交量萎缩 -> 可能流动性不足
        large_price = (pct_chg.abs() > 0.01).astype(float)
        low_volume = (vol_chg.abs() < 0.05).astype(float)
        risk2 = large_price * low_volume
        risk = (risk1 + risk2).clip(0, 1)
        # 映射到[-1,1]
        result = risk * 2 - 1
        return result.fillna(-1)  # 默认无风险
