"""AI因子: 持仓超时风险 | 置信:60% | 度量价格在窄幅区间内的持续震荡时长，当价格长时间无法突破时，容易触发持仓超时止损。因子计算过去N根K线内价格波动的窄幅占比，并用成交量萎缩确认。窄幅震荡时间越长，因子值越接近-1（负面风险），否则接近0或正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRisk(BaseFactor):
    """度量价格在窄幅区间内的持续震荡时长，当价格长时间无法突破时，容易触发持仓超时止损。因子计算过去N根K线内价格波动的窄幅占比，并用成交量萎缩确认。窄幅震荡时间越长，因子值越接近-1（负面风险），否则接近0或正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdrisk",
            name="Hold Timeout Risk",
            display_name="持仓超时风险",
            description="度量价格在窄幅区间内的持续震荡时长，当价格长时间无法突破时，容易触发持仓超时止损。因子计算过去N根K线内价格波动的窄幅占比，并用成交量萎缩确认。窄幅震荡时间越长，因子值越接近-1（负面风险），否则接近0或正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        lookback = 30
        range_threshold = 0.02  # 2%价格波动视为窄幅
        # 计算每根K线的真实波幅百分比
        true_range = np.maximum(data['high'] - data['low'],
                                np.maximum(np.abs(data['high'] - data['close'].shift(1)),
                                           np.abs(data['low'] - data['close'].shift(1))))
        range_pct = true_range / data['close'].shift(1)
        # 标记窄幅K线
        narrow = (range_pct < range_threshold).fillna(False).astype(int)
        # 过去lookback根中窄幅K线占比
        narrow_ratio = narrow.rolling(lookback, min_periods=lookback//2).sum() / lookback
        # 成交量萎缩确认
        vol_avg = data['volume'].rolling(lookback, min_periods=lookback//2).mean()
        vol_shrink = (data['volume'] < vol_avg * 0.8).astype(float)
        # 综合风险得分：窄幅占比高且成交量萎缩时风险最大
        risk_score = -1.0 * (narrow_ratio > 0.6).astype(float) * (vol_shrink > 0)
        # 平滑/归一化到[-1,1]
        result = pd.Series(risk_score, index=data.index)
        return result
