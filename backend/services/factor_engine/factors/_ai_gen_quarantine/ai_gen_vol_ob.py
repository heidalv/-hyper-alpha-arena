"""AI因子: 波动率调整超买风险因子 | 置信:60% | 近期涨幅除以波动率（如ATR），衡量单位风险下的回报。当该值过高时，市场处于超买状态，容易触发止损或快速反转，导致多头亏损。因子值接近+1表示超买风险极高，接近-1表示超卖或安全区域。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedOverboughtRisk(BaseFactor):
    """近期涨幅除以波动率（如ATR），衡量单位风险下的回报。当该值过高时，市场处于超买状态，容易触发止损或快速反转，导致多头亏损。因子值接近+1表示超买风险极高，接近-1表示超卖或安全区域。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ob",
            name="Volatility-Adjusted Overbought Risk",
            display_name="波动率调整超买风险因子",
            description="近期涨幅除以波动率（如ATR），衡量单位风险下的回报。当该值过高时，市场处于超买状态，容易触发止损或快速反转，导致多头亏损。因子值接近+1表示超买风险极高，接近-1表示超卖或安全区域。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean()
        # 5日价格变化
        returns_5 = close.pct_change(5)
        # 单位风险收益
        risk_adj_return = returns_5 / (atr / close.shift(1))
        # 用滚动Z-score标准化
        z = (risk_adj_return - risk_adj_return.rolling(60).mean()) / risk_adj_return.rolling(60).std()
        # 使用tanh映射到[-1,1]
        result = np.tanh(z / 2.0)
        return result
