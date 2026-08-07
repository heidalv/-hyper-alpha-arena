"""AI因子: 反向套利亏损风险因子 | 置信:55% | 计算过去10周期价格序列的一阶自相关系数，当自相关接近0（绝对值<0.2）且波动率较高时，认为价格随机性强，容易导致reverse_netting策略失效，输出负值；反之为正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingRisk(BaseFactor):
    """计算过去10周期价格序列的一阶自相关系数，当自相关接近0（绝对值<0.2）且波动率较高时，认为价格随机性强，容易导致reverse_netting策略失效，输出负值；反之为正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvnt",
            name="Reverse Netting Risk",
            display_name="反向套利亏损风险因子",
            description="计算过去10周期价格序列的一阶自相关系数，当自相关接近0（绝对值<0.2）且波动率较高时，认为价格随机性强，容易导致reverse_netting策略失效，输出负值；反之为正值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        ret = np.diff(close) / close[:-1]
        period = 10
        n = len(ret)
        autocorr = np.full(n+1, np.nan)
        for i in range(period, n+1):
            eps = ret[i-period:i]
            if np.std(eps) == 0:
                autocorr[i] = 0
            else:
                autocorr[i] = np.corrcoef(eps[:-1], eps[1:])[0,1]
        # volatility of returns
        vol = np.concatenate([[np.nan], np.array([np.std(ret[i-period:i]) for i in range(period, n+1)])])
        raw = np.where((np.abs(autocorr) < 0.2) & (vol > np.nanpercentile(vol, 70)), -1.0, 1.0)
        result = pd.Series(raw, index=data.index)
        return result
