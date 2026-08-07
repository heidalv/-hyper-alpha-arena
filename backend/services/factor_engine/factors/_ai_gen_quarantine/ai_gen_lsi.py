"""AI因子: 流动性冲击指标 | 置信:60% | 衡量价格对成交量变化的敏感度，计算过去N周期价格变动与成交量变动的相关系数绝对值。高敏感度意味着低流动性或操纵风险，容易导致滑点和意外亏损（如master_running等模式）。因子值越低表示流动性风险越高，应做空或回避。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Shock_Indicator(BaseFactor):
    """衡量价格对成交量变化的敏感度，计算过去N周期价格变动与成交量变动的相关系数绝对值。高敏感度意味着低流动性或操纵风险，容易导致滑点和意外亏损（如master_running等模式）。因子值越低表示流动性风险越高，应做空或回避。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lsi",
            name="Liquidity Shock Indicator",
            display_name="流动性冲击指标",
            description="衡量价格对成交量变化的敏感度，计算过去N周期价格变动与成交量变动的相关系数绝对值。高敏感度意味着低流动性或操纵风险，容易导致滑点和意外亏损（如master_running等模式）。因子值越低表示流动性风险越高，应做空或回避。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 10
        # 价格变化百分比
        price_chg = data['close'].pct_change()
        # 成交量变化率
        vol_chg = data['volume'].pct_change()
        # 滚动相关系数绝对值
        def rolling_corr_abs(x, y):
            return abs(x.rolling(n).corr(y))
        corr_abs = rolling_corr_abs(price_chg, vol_chg)
        # 因子：1 - 相关系数，使得高风险时因子低
        factor = 1 - corr_abs
        # 标准化到[-1,1] (通常corr在0~1，所以factor在0~1，映射到-1~1)
        result = pd.Series(factor * 2 - 1, index=data.index)
        return result.fillna(0.0)
