"""AI因子: 趋势反转概率因子 | 置信:60% | 基于价格相对于近期高点和低点的位置以及动量动量变化，识别潜在反转。当价格接近近期高点且动量减弱时，做空可能面临反转风险；反之接近低点且动量增强时，做空相对安全。因子值+1表示反转概率高（做空危险），-1表示趋势延续适合做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendReversalProbability(BaseFactor):
    """基于价格相对于近期高点和低点的位置以及动量动量变化，识别潜在反转。当价格接近近期高点且动量减弱时，做空可能面临反转风险；反之接近低点且动量增强时，做空相对安全。因子值+1表示反转概率高（做空危险），-1表示趋势延续适合做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_rev",
            name="Trend Reversal Probability",
            display_name="趋势反转概率因子",
            description="基于价格相对于近期高点和低点的位置以及动量动量变化，识别潜在反转。当价格接近近期高点且动量减弱时，做空可能面临反转风险；反之接近低点且动量增强时，做空相对安全。因子值+1表示反转概率高（做空危险），-1表示趋势延续适合做空。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算近期最高最低
        n = 14
        recent_high = high.rolling(n).max()
        recent_low = low.rolling(n).min()
        # 价格在区间内的位置（0~1）
        range_ = recent_high - recent_low
        range_safe = range_.replace(0, np.nan).ffill().fillna(1)
        position = (close - recent_low) / range_safe
        # 动量：过去5日收益率
        ret5 = close.pct_change(5)
        # 反向指标：当位置高且动量衰减（由正转负或减速）时，反转概率大
        # 构造得分：位置p和动量m的组合
        p = position * 2 - 1  # 映射到[-1,1] 高位接近+1，低位接近-1
        m = np.tanh(ret5 * 10)  # 动量映射到[-1,1]
        # 反转信号：如果p高且m转负（即高位动量减弱），得分正；如果p低且m转正，得分负
        # 使用 p * ( -m )：高位且负动量 => 正；低位且正动量 => 负
        score = -p * m
        # 平滑
        result = score.rolling(3).mean().ffill().fillna(0)
        return pd.Series(result, index=data.index)
