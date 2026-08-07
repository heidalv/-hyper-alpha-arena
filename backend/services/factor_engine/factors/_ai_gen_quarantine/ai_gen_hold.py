"""AI因子: 持仓超时风险因子 | 置信:60% | 基于价格趋势持续性与波动率变化，预测持仓超时可能导致的亏损。当价格在窄幅区间内长时间盘整且波动率收缩时，容易触发超时平仓。通过计算ATR与价格波动的比率来度量。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRisk(BaseFactor):
    """基于价格趋势持续性与波动率变化，预测持仓超时可能导致的亏损。当价格在窄幅区间内长时间盘整且波动率收缩时，容易触发超时平仓。通过计算ATR与价格波动的比率来度量。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold",
            name="HoldTimeoutRisk",
            display_name="持仓超时风险因子",
            description="基于价格趋势持续性与波动率变化，预测持仓超时可能导致的亏损。当价格在窄幅区间内长时间盘整且波动率收缩时，容易触发超时平仓。通过计算ATR与价格波动的比率来度量。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR（14周期）
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 价格波动范围（过去n天最高-最低）
        n = 10
        price_range = data['high'].rolling(n).max() - data['low'].rolling(n).min()
        # 波动率收缩：ATR相对价格范围的比值下降
        ratio = atr / price_range.replace(0, np.nan)
        # 取近期变化率
        ratio_change = ratio.pct_change(5)
        # 当ratio下降（波动收缩）且价格波动小，风险增加
        raw = -ratio_change * (1 / (price_range + 1e-8))
        # 标准化
        std = raw.rolling(20).std().replace(0, np.nan)
        result = raw / std
        result = result.clip(-3, 3) / 3
        return result.fillna(0)
