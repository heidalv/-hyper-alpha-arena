"""AI因子: 量价背离信号 | 置信:55% | 检测价格与成交量之间的背离：价格上涨但成交量萎缩（多头衰竭），或价格下跌但成交量萎缩（空头衰竭），预示反转。使用价格变化率与成交量变化率的相关系数。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """检测价格与成交量之间的背离：价格上涨但成交量萎缩（多头衰竭），或价格下跌但成交量萎缩（空头衰竭），预示反转。使用价格变化率与成交量变化率的相关系数。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离信号",
            description="检测价格与成交量之间的背离：价格上涨但成交量萎缩（多头衰竭），或价格下跌但成交量萎缩（空头衰竭），预示反转。使用价格变化率与成交量变化率的相关系数。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        close = data['close']
        volume = data['volume']
        # 计算价格变化率（对数收益率）
        ret = close.pct_change()
        # 成交量变化率
        vol_chg = volume.pct_change()
        # 计算过去N天的滚动相关系数（价格变化与成交量变化）
        N = 10
        corr = ret.rolling(N).corr(vol_chg)
        # 背离信号：相关系数负且绝对值大，表示价量背离
        # 如果相关系数为负且显著，则预期反转
        # 同时结合价格方向：若价格下跌而相关系数为负，是买入信号（正值）；价格上涨而相关系数为负，是卖出信号（负值）
        # 用价格方向调整
        price_trend = close / close.shift(N) - 1
        signal = -corr * price_trend
        # 通过tanh映射到[-1,1]
        result = np.tanh(signal * 5)
        result = result.fillna(0)
        return result
