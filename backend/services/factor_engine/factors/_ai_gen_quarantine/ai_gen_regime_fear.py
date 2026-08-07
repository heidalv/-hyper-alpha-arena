"""AI因子: 市场状态恐惧指数 | 置信:60% | 基于ATR与价格相对位置，识别高波动且无明显趋势的市场状态。当ATR处于近期高位且价格在布林带中轨附近震荡时，表明市场处于未知状态，因子输出负值以提示风险。计算方式：计算过去20日ATR的百分位，以及价格偏离20日均线的百分比，结合两者得到综合得分，映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeFearIndex(BaseFactor):
    """基于ATR与价格相对位置，识别高波动且无明显趋势的市场状态。当ATR处于近期高位且价格在布林带中轨附近震荡时，表明市场处于未知状态，因子输出负值以提示风险。计算方式：计算过去20日ATR的百分位，以及价格偏离20日均线的百分比，结合两者得到综合得分，映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_fear",
            name="Regime Fear Index",
            display_name="市场状态恐惧指数",
            description="基于ATR与价格相对位置，识别高波动且无明显趋势的市场状态。当ATR处于近期高位且价格在布林带中轨附近震荡时，表明市场处于未知状态，因子输出负值以提示风险。计算方式：计算过去20日ATR的百分位，以及价格偏离20日均线的百分比，结合两者得到综合得分，映射到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(20).mean()
        atr_pct = atr / close * 100
        atr_rank = atr_pct.rank(pct=True)  # 0-1
        # 价格偏离均线
        ma20 = close.rolling(20).mean()
        pct_dev = (close - ma20) / ma20 * 100
        dev_abs = np.abs(pct_dev)
        # 高波动且低趋势：ATR高且价格偏离小 -> 负值
        raw = atr_rank - dev_abs / 10  # 调节
        result = 2 * (raw - 0.5)  # 映射到[-1,1]
        return result.clip(-1, 1)
