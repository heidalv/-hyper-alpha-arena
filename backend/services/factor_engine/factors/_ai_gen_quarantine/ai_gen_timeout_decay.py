"""AI因子: 持仓超时衰减 | 置信:60% | 模拟持仓时间过长导致的收益衰减与反转。通过计算价格相对于一段时间（如20周期）起点位置的回归强度，结合日内波动衰减，捕捉趋势衰竭后回归均值的倾向。适用于解释hold_timeout_review和max_hold_timeout亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PositionHoldingTimeoutDecay(BaseFactor):
    """模拟持仓时间过长导致的收益衰减与反转。通过计算价格相对于一段时间（如20周期）起点位置的回归强度，结合日内波动衰减，捕捉趋势衰竭后回归均值的倾向。适用于解释hold_timeout_review和max_hold_timeout亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_decay",
            name="Position Holding Timeout Decay",
            display_name="持仓超时衰减",
            description="模拟持仓时间过长导致的收益衰减与反转。通过计算价格相对于一段时间（如20周期）起点位置的回归强度，结合日内波动衰减，捕捉趋势衰竭后回归均值的倾向。适用于解释hold_timeout_review和max_hold_timeout亏损。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 参数
        hold_period = 30  # 模拟持仓周期
        decay_rate = 0.05
        # 当前价格相对于 hold_period 之前的价格位置
        price_change = data['close'] / data['close'].shift(hold_period) - 1
        # 移动平均回归强度：当前价格距移动平均的偏离
        ma = data['close'].rolling(hold_period).mean()
        deviation = (data['close'] - ma) / ma
        # 模拟持仓时间越长，回归信号越强（用累积波动率修正）
        cumulative_vol = data['close'].pct_change().rolling(hold_period).std()
        # 综合信号：当价格偏离均线且变化率较大时，给予反向信号
        signal = -np.sign(deviation) * (np.abs(deviation) / (cumulative_vol + 1e-10)) * decay_rate
        signal = np.clip(signal, -1, 1)
        result = pd.Series(signal, index=data.index).fillna(0)
        return result
