"""AI因子: 持仓超时风险 | 置信:60% | 识别窄幅震荡、缺乏方向性波动的市场状态，这种状态易导致持仓超时止损。通过计算过去N周期内价格变动幅度与ATR的比值，以及价格在布林带内的位置。低波动且价格在布林带中轨附近时风险高。因子值为正表示超时风险高，负表示有利持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRisk(BaseFactor):
    """识别窄幅震荡、缺乏方向性波动的市场状态，这种状态易导致持仓超时止损。通过计算过去N周期内价格变动幅度与ATR的比值，以及价格在布林带内的位置。低波动且价格在布林带中轨附近时风险高。因子值为正表示超时风险高，负表示有利持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout_risk",
            name="Hold Timeout Risk",
            display_name="持仓超时风险",
            description="识别窄幅震荡、缺乏方向性波动的市场状态，这种状态易导致持仓超时止损。通过计算过去N周期内价格变动幅度与ATR的比值，以及价格在布林带内的位置。低波动且价格在布林带中轨附近时风险高。因子值为正表示超时风险高，负表示有利持仓。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR（14）
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 计算过去20周期价格变动幅度（最高-最低相对于ATR）
        range20 = high.rolling(20).max() - low.rolling(20).min()
        norm_range = range20 / (atr * 20)  # 单位ATR下的波动范围
        # 计算布林带位置（20周期，2倍标准差）
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_position = (close - sma20) / (2 * std20 + 1e-10)  # [-1,1]附近
        # 当norm_range较小且bb_position接近0时，表示窄幅横盘
        risk = (1 - norm_range) * (1 - np.abs(bb_position))
        # 归一化到[-1,1]
        result = 2 * (risk - risk.rolling(100).min()) / (risk.rolling(100).max() - risk.rolling(100).min() + 1e-10) - 1
        return result.fillna(0).clip(-1, 1)
