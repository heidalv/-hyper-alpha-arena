"""AI因子: 持仓超时风险 | 置信:60% | 检测连续同向运行天数与波动率收缩的耦合，识别趋势衰竭导致的超时止损风险。连续上涨或下跌天数越长且波动率下降越明显，风险越高。输出正值表示高风险，负值表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeout_Risk(BaseFactor):
    """检测连续同向运行天数与波动率收缩的耦合，识别趋势衰竭导致的超时止损风险。连续上涨或下跌天数越长且波动率下降越明显，风险越高。输出正值表示高风险，负值表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_to",
            name="Timeout Risk",
            display_name="持仓超时风险",
            description="检测连续同向运行天数与波动率收缩的耦合，识别趋势衰竭导致的超时止损风险。连续上涨或下跌天数越长且波动率下降越明显，风险越高。输出正值表示高风险，负值表示低风险。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 连续上涨天数（close > 前一日close）
        up = (close > close.shift(1)).astype(int)
        consec_up = up * (up + up.shift(1).fillna(0))  # 简单累加
        for i in range(2, 10):
            consec_up = up * (consec_up + up.shift(i).fillna(0))
        # 连续下跌天数
        down = (close < close.shift(1)).astype(int)
        consec_down = down * (down + down.shift(1).fillna(0))
        for i in range(2, 10):
            consec_down = down * (consec_down + down.shift(i).fillna(0))
        consec = np.maximum(consec_up, consec_down)  # 取绝对值更大的连续天数
        # 波动率收缩: 5日ATR / 20日ATR，越小表示波动率收缩
        atr5 = (high - low).rolling(5).mean()
        atr20 = (high - low).rolling(20).mean()
        vol_shrink = atr5 / (atr20 + 1e-8)
        # 风险信号: 连续天数 * (1 - vol_shrink)，当vol_shrink小且连续天数为正时风险高
        raw = consec * (1 - vol_shrink) / 10.0  # 缩放因子
        result = np.tanh(raw - 0.5)  # 偏移使中心在0附近
        return result
