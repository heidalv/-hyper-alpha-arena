"""AI因子: 成交量趋势一致性 | 置信:60% | 衡量价格趋势与成交量趋势的方向一致性。当价格上涨但成交量萎缩，或价格下跌且成交量放大时，趋势不可靠。因子输出正表示一致（健康），负表示不一致（陷阱）。使用5日价格斜率与5日成交量斜率的乘积，经tanh归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeTrendConsistency(BaseFactor):
    """衡量价格趋势与成交量趋势的方向一致性。当价格上涨但成交量萎缩，或价格下跌且成交量放大时，趋势不可靠。因子输出正表示一致（健康），负表示不一致（陷阱）。使用5日价格斜率与5日成交量斜率的乘积，经tanh归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_trend_con",
            name="Volume-Trend Consistency",
            display_name="成交量趋势一致性",
            description="衡量价格趋势与成交量趋势的方向一致性。当价格上涨但成交量萎缩，或价格下跌且成交量放大时，趋势不可靠。因子输出正表示一致（健康），负表示不一致（陷阱）。使用5日价格斜率与5日成交量斜率的乘积，经tanh归一化。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算5日价格斜率（线性回归斜率简化）
        close = data['close']
        volume = data['volume']
        # 使用差分近似斜率：最近5日平均值变化
        price_slope = close.rolling(5).mean().diff(1)
        vol_slope = volume.rolling(5).mean().diff(1)
        # 标准化为z-score（滚动60日）
        price_z = (price_slope - price_slope.rolling(60).mean()) / price_slope.rolling(60).std()
        vol_z = (vol_slope - vol_slope.rolling(60).mean()) / vol_slope.rolling(60).std()
        # 一致性：同向为正，反向为负
        signal = price_z * vol_z
        result = np.tanh(signal)
        return result.fillna(0.0)
