"""AI因子: 反向净仓动量 | 置信:60% | 捕捉短期动量与长期趋势背离：价格创近期新低但短期动量指标转正，或创近期新高但短期动量转负。结合成交量确认，指示潜在反转。因子值>0表示看涨背离（新低后动量转正），<0表示看跌背离（新高后动量转负）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversenettingmomentum(BaseFactor):
    """捕捉短期动量与长期趋势背离：价格创近期新低但短期动量指标转正，或创近期新高但短期动量转负。结合成交量确认，指示潜在反转。因子值>0表示看涨背离（新低后动量转正），<0表示看跌背离（新高后动量转负）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reverse_net",
            name="ReverseNettingMomentum",
            display_name="反向净仓动量",
            description="捕捉短期动量与长期趋势背离：价格创近期新低但短期动量指标转正，或创近期新高但短期动量转负。结合成交量确认，指示潜在反转。因子值>0表示看涨背离（新低后动量转正），<0表示看跌背离（新高后动量转负）。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        momentum_window = 5  # 短期动量计算周期
        trend_window = 20    # 长期趋势参考周期
        # 计算短期动量（ROC）
        roc = data['close'].pct_change(periods=momentum_window)
        # 计算近期最低/最高（趋势窗口内）
        recent_low = data['low'].rolling(trend_window, min_periods=1).min()
        recent_high = data['high'].rolling(trend_window, min_periods=1).max()
        # 当前价格是否接近近期极值（允许微小偏差）
        near_low = data['close'] <= recent_low.shift(1) * 1.001
        near_high = data['close'] >= recent_high.shift(1) * 0.999
        # 动量背离条件
        # 新低但动量转正：当前接近新低且短期ROC从负转正（动量上升）
        # 使用动量符号变化：前一周期动量负，当前动量正
        roc_prev = roc.shift(1)
        bullish_div = near_low & (roc_prev < 0) & (roc > 0)
        # 新高但动量转负：当前接近新高且短期ROC从正转负
        bearish_div = near_high & (roc_prev > 0) & (roc < 0)
        # 成交量确认（可选）：动量转向时成交量放大
        vol_ratio = data['volume'] / data['volume'].rolling(trend_window, min_periods=1).mean().shift(1)
        vol_confirm = vol_ratio > 1.2
        # 信号
        signal = pd.Series(0.0, index=data.index)
        signal[bullish_div & vol_confirm] = 1.0
        signal[bearish_div & vol_confirm] = -1.0
        return signal
