"""AI因子: 趋势稳定性 | 置信:50% | 通过计算价格序列的短期自相关性，衡量趋势的稳定性。当趋势稳定（自相关强）时，因子接近-1；当趋势混乱（自相关接近0或负）时，因子接近+1，表示未知风险状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Stability(BaseFactor):
    """通过计算价格序列的短期自相关性，衡量趋势的稳定性。当趋势稳定（自相关强）时，因子接近-1；当趋势混乱（自相关接近0或负）时，因子接近+1，表示未知风险状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trndstb",
            name="Trend Stability",
            display_name="趋势稳定性",
            description="通过计算价格序列的短期自相关性，衡量趋势的稳定性。当趋势稳定（自相关强）时，因子接近-1；当趋势混乱（自相关接近0或负）时，因子接近+1，表示未知风险状态。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 使用收盘价对数收益率
        ret = np.log(data['close']).diff()
        # 计算过去5天和10天的自相关系数（滞后1期）
        def rolling_autocorr(series, window, lag=1):
            def _autocorr(x):
                if len(x) < window:
                    return np.nan
                s = pd.Series(x)
                return s.autocorr(lag=lag)
            return series.rolling(window).apply(lambda x: _autocorr(x.values), raw=False)
        autocorr5 = rolling_autocorr(ret, 5, 1)
        autocorr10 = rolling_autocorr(ret, 10, 1)
        # 取平均，如果缺失则用另一个
        combined = autocorr5.fillna(autocorr10)
        # 将自相关值映射到[-1,1]：当自相关>0.5时为-1，<-0.5时为1，中间线性
        result = -np.clip(combined, -1, 1)
        # 调整方向，使正相关时为负（稳定），负相关时为正（不稳定）
        return pd.Series(result, index=data.index).fillna(0)
