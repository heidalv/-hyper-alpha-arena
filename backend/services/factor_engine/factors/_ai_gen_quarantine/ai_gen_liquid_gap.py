"""AI因子: 流动性缺口因子 | 置信:65% | 检测价格突破关键位时成交量不足导致的流动性陷阱，通过计算价格移动极值与成交量的背离程度，当价格创n日新高或新低但成交量处于低分位时发出风险信号。模拟dust_cleanup和holding_timeout_review中的流动性问题。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityGap(BaseFactor):
    """检测价格突破关键位时成交量不足导致的流动性陷阱，通过计算价格移动极值与成交量的背离程度，当价格创n日新高或新低但成交量处于低分位时发出风险信号。模拟dust_cleanup和holding_timeout_review中的流动性问题。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquid_gap",
            name="Liquidity Gap",
            display_name="流动性缺口因子",
            description="检测价格突破关键位时成交量不足导致的流动性陷阱，通过计算价格移动极值与成交量的背离程度，当价格创n日新高或新低但成交量处于低分位时发出风险信号。模拟dust_cleanup和holding_timeout_review中的流动性问题。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14  # 观察窗口
        # 价格区间位置：当前位置在n日高低中的位置
        high_roll = data['high'].rolling(n).max()
        low_roll = data['low'].rolling(n).min()
        range_pos = (data['close'] - low_roll) / (high_roll - low_roll + 1e-10)
        # 成交量分位：当前成交量在n日中的分位
        vol_rank = data['volume'].rolling(n).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 背离信号：价格靠近极端位置但成交量低迷
        # 价格靠近上轨(>0.8)且成交量低分位(<0.2) -> 看空风险
        # 价格靠近下轨(<0.2)且成交量低分位(<0.2) -> 看多风险
        bear_sig = (range_pos > 0.8) & (vol_rank < 0.2)
        bull_sig = (range_pos < 0.2) & (vol_rank < 0.2)
        # 合成信号：正值表示多头风险（应避免做多），负值表示空头风险
        result = bear_sig.astype(float) * 1.0 - bull_sig.astype(float) * 1.0
        # 连续平滑
        result = result.rolling(3).mean()
        return result.fillna(0)
