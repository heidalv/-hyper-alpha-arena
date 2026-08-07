"""AI因子: 市场状态风险指标 | 置信:60% | 基于ATR与成交量的异常变化，识别高风险的未知市场状态。当短期波动率相对历史均值显著放大且成交量异常时，因子值接近+1，表示高风险；反之接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeRiskIndicator(BaseFactor):
    """基于ATR与成交量的异常变化，识别高风险的未知市场状态。当短期波动率相对历史均值显著放大且成交量异常时，因子值接近+1，表示高风险；反之接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_risk",
            name="Regime Risk Indicator",
            display_name="市场状态风险指标",
            description="基于ATR与成交量的异常变化，识别高风险的未知市场状态。当短期波动率相对历史均值显著放大且成交量异常时，因子值接近+1，表示高风险；反之接近-1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = np.maximum(high_low, np.maximum(high_close, low_close))
        atr = tr.rolling(window=20).mean()
        # 波动率偏离：当前ATR相对20日ATR均值的比率
        atr_ratio = atr / atr.rolling(window=20).mean() - 1
        # 成交量异常：当前成交量相对20日均值的偏离
        vol_mean = data['volume'].rolling(window=20).mean()
        vol_ratio = (data['volume'] / vol_mean) - 1
        # 综合得分，使用tanh归一化到[-1,1]
        combined = 0.6 * atr_ratio + 0.4 * vol_ratio
        result = np.tanh(combined * 2)
        # 处理前几个NaN
        result = result.fillna(0.0)
        return result
