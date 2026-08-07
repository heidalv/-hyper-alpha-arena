"""AI因子: 反转风险 | 置信:60% | 检测价格接近近期极值（前N根K线最高/最低）且成交量异常放大时的反转风险。价格接近高点时看空（负值），接近低点时看多（正值），风险越高绝对值越大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversalrisk(BaseFactor):
    """检测价格接近近期极值（前N根K线最高/最低）且成交量异常放大时的反转风险。价格接近高点时看空（负值），接近低点时看多（正值），风险越高绝对值越大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rr",
            name="ReversalRisk",
            display_name="反转风险",
            description="检测价格接近近期极值（前N根K线最高/最低）且成交量异常放大时的反转风险。价格接近高点时看空（负值），接近低点时看多（正值），风险越高绝对值越大。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        period = 20
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 近期极值
        recent_high = high.rolling(period).max()
        recent_low = low.rolling(period).min()
        # 价格位置百分比
        range_ = recent_high - recent_low
        range_ = range_.replace(0, np.nan)  # 避免除0
        position = (close - recent_low) / range_  # 0~1
        # 成交量放大系数：当前成交量相对过去均值
        avg_vol = volume.rolling(period).mean()
        vol_ratio = volume / avg_vol
        # 反转信号：位置接近0或1且成交量放大
        risk = np.where(position > 0.9, -1 * vol_ratio, np.where(position < 0.1, 1 * vol_ratio, 0))
        # 归一化到[-1,1]
        risk_series = pd.Series(risk, index=data.index)
        # 使用tanh限制极端值
        result = np.tanh(risk_series * 0.5)
        return result.fillna(0).clip(-1,1)
