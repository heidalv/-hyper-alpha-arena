"""AI因子: 反转强度 | 置信:70% | 计算最近N根K线内价格从极值回来的幅度，结合成交量放大识别流动性磁铁反转风险。当价格创近期新高/新低后迅速反转且成交量异常时，因子值接近-1（强烈看空反转）或+1（强烈看多反转），否则接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalIntensity(BaseFactor):
    """计算最近N根K线内价格从极值回来的幅度，结合成交量放大识别流动性磁铁反转风险。当价格创近期新高/新低后迅速反转且成交量异常时，因子值接近-1（强烈看空反转）或+1（强烈看多反转），否则接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revint",
            name="Reversal Intensity",
            display_name="反转强度",
            description="计算最近N根K线内价格从极值回来的幅度，结合成交量放大识别流动性磁铁反转风险。当价格创近期新高/新低后迅速反转且成交量异常时，因子值接近-1（强烈看空反转）或+1（强烈看多反转），否则接近0。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        # 参数
        lookback = 20
        extreme_window = 5
        # 最近N根K线的最高价和最低价
        rolling_high = data['high'].rolling(lookback, min_periods=1).max()
        rolling_low = data['low'].rolling(lookback, min_periods=1).min()
        # 当前价格相对于极值的位置
        upper_dist = (rolling_high - data['close']) / (rolling_high - rolling_low + 1e-10)
        lower_dist = (data['close'] - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 近期反转信号：价格创极值后快速回归
        # 创近期新高后回撤幅度
        new_high = data['high'] == rolling_high
        new_low = data['low'] == rolling_low
        # 计算过去extreme_window根K线内出现新高的次数
        high_count = new_high.rolling(extreme_window, min_periods=1).sum()
        low_count = new_low.rolling(extreme_window, min_periods=1).sum()
        # 反转强度：若近期有新高且当前价格远离高点则为看空反转
        bearish_rev = (high_count > 0) & (upper_dist > 0.5)
        bullish_rev = (low_count > 0) & (lower_dist > 0.5)
        # 成交量放大因子
        vol_ratio = data['volume'] / data['volume'].rolling(lookback, min_periods=1).mean()
        vol_surge = vol_ratio > 1.5
        # 综合得分
        rev_score = np.where(bearish_rev & vol_surge, -1.0,
                    np.where(bullish_rev & vol_surge, 1.0, 0.0))
        # 平滑处理（可选）
        result = pd.Series(rev_score, index=data.index)
        return result
