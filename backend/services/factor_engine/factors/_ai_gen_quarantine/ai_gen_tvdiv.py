"""AI因子: 趋势波动背离 | 置信:60% | 当价格短期波动微弱（平均真实波幅ATR相对较低）但波动率指标（如历史波动率HV）上升时，表明市场缺乏明确方向但潜在风险增大，容易导致假突破和止损亏损。该因子在背离时输出负值，提示避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Volatility_Divergence(BaseFactor):
    """当价格短期波动微弱（平均真实波幅ATR相对较低）但波动率指标（如历史波动率HV）上升时，表明市场缺乏明确方向但潜在风险增大，容易导致假突破和止损亏损。该因子在背离时输出负值，提示避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tvdiv",
            name="Trend_Volatility_Divergence",
            display_name="趋势波动背离",
            description="当价格短期波动微弱（平均真实波幅ATR相对较低）但波动率指标（如历史波动率HV）上升时，表明市场缺乏明确方向但潜在风险增大，容易导致假突破和止损亏损。该因子在背离时输出负值，提示避免做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR (14周期)
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 归一化ATR到价格比例
        atr_ratio = atr / close
        # 计算历史波动率 (20日收益率标准差年化)
        ret = close.pct_change()
        hv = ret.rolling(20).std() * np.sqrt(252)
        # 计算ATR的短期变化率 (5日差分)
        atr_change = atr_ratio.pct_change(5)
        hv_change = hv.pct_change(5)
        # 背离：ATR稳定或下降，但HV上升 -> 信号负向
        divergence = np.where((atr_change < 0) & (hv_change > 0), -1, 0)
        # 平滑并映射到[-1,1]
        result = pd.Series(divergence, index=data.index).rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
