"""AI因子: 反转陷阱指标 | 置信:60% | 捕捉短期动量与价格位置背离的情况。当RSI进入超买（>70）但价格未能创近期新高，或RSI进入超卖（<30）但价格未能创近期新低时，容易发生‘ai_reverse’或止损反转陷阱。因子值负向表示存在反转风险，正向表示趋势正常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Trap_Indicator(BaseFactor):
    """捕捉短期动量与价格位置背离的情况。当RSI进入超买（>70）但价格未能创近期新高，或RSI进入超卖（<30）但价格未能创近期新低时，容易发生‘ai_reverse’或止损反转陷阱。因子值负向表示存在反转风险，正向表示趋势正常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reverse_trap",
            name="Reversal Trap Indicator",
            display_name="反转陷阱指标",
            description="捕捉短期动量与价格位置背离的情况。当RSI进入超买（>70）但价格未能创近期新高，或RSI进入超卖（<30）但价格未能创近期新低时，容易发生‘ai_reverse’或止损反转陷阱。因子值负向表示存在反转风险，正向表示趋势正常。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=14).mean()
        avg_loss = loss.rolling(14, min_periods=14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 近期最高/最低（10日）
        recent_high = high.rolling(10, min_periods=10).max()
        recent_low = low.rolling(10, min_periods=10).min()
        # 超买且价格未破新高（低于最高价的99%）
        overbought_trap = (rsi > 70) & (close < recent_high * 0.99)
        # 超卖且价格未破新低（高于最低价的101%）
        oversold_trap = (rsi < 30) & (close > recent_low * 1.01)
        # 组合信号：陷阱时输出-1，否则+1
        trap_condition = overbought_trap | oversold_trap
        result = np.where(trap_condition, -1.0, 1.0)
        # 平滑处理：用滚动均值使其连续化
        result_series = pd.Series(result, index=close.index)
        result_smooth = result_series.rolling(3, min_periods=1).mean()
        return result_smooth
