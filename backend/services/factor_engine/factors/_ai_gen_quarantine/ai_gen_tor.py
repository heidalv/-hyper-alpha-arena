"""AI因子: 超时风险因子 | 置信:60% | 针对持仓超时导致的亏损，度量市场横盘震荡程度。计算过去24根K线内价格的最大变化幅度（最高-最低）与收盘价均值之比，若比值低于历史20%分位数，则表示价格长期窄幅震荡，容易触发超时止损，输出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeOut_Risk(BaseFactor):
    """针对持仓超时导致的亏损，度量市场横盘震荡程度。计算过去24根K线内价格的最大变化幅度（最高-最低）与收盘价均值之比，若比值低于历史20%分位数，则表示价格长期窄幅震荡，容易触发超时止损，输出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tor",
            name="TimeOut_Risk",
            display_name="超时风险因子",
            description="针对持仓超时导致的亏损，度量市场横盘震荡程度。计算过去24根K线内价格的最大变化幅度（最高-最低）与收盘价均值之比，若比值低于历史20%分位数，则表示价格长期窄幅震荡，容易触发超时止损，输出负向信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 过去24期价格极差比率
        window = 24
        high_max = data['high'].rolling(window).max()
        low_min = data['low'].rolling(window).min()
        range_ratio = (high_max - low_min) / data['close'].rolling(window).mean()
        # 滚动历史分位比较（60期参考）
        def rolling_quantile(series, q, win):
            return series.rolling(win).quantile(q)
        q20 = rolling_quantile(range_ratio, 0.2, 120)
        # 如果当前值低于20%分位数，则认为横盘风险高
        risk = (range_ratio < q20).astype(float)
        # 映射到[-1,1]，横盘风险高时-1，否则+1
        result = np.where(risk, -1.0, 1.0)
        return pd.Series(result, index=data.index).fillna(0)
