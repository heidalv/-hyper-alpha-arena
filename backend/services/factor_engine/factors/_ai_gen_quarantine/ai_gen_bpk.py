"""AI因子: 牛力袋鼠指标 | 置信:60% | 结合短期动量与成交量确认。计算最近N日价格变化与同期成交量变化的协方差，衡量趋势强度。当协方差为负且绝对值大时，表明量价不同步，趋势脆弱，容易产生止损/止盈失败。输出归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bull_Power_Kangaroo(BaseFactor):
    """结合短期动量与成交量确认。计算最近N日价格变化与同期成交量变化的协方差，衡量趋势强度。当协方差为负且绝对值大时，表明量价不同步，趋势脆弱，容易产生止损/止盈失败。输出归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bpk",
            name="Bull-Power Kangaroo",
            display_name="牛力袋鼠指标",
            description="结合短期动量与成交量确认。计算最近N日价格变化与同期成交量变化的协方差，衡量趋势强度。当协方差为负且绝对值大时，表明量价不同步，趋势脆弱，容易产生止损/止盈失败。输出归一化到[-1,1]。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算5日价格变化
        price_chg = data['close'].diff(5)
        # 计算5日成交量变化（对数差值）
        vol_chg = np.log(data['volume'] / data['volume'].shift(5) + 1e-10)
        # 滚动协方差（20日窗口）
        cov = price_chg.rolling(20).cov(vol_chg)
        # 滚动方差标准化
        var_price = price_chg.rolling(20).var()
        var_vol = vol_chg.rolling(20).var()
        corr = cov / (np.sqrt(var_price * var_vol) + 1e-10)
        # 取负值：负相关越强，因子越负（弱趋势风险高）
        raw = -corr
        # 截断到[-1,1]
        result = np.clip(raw, -1, 1)
        return result.fillna(0)
