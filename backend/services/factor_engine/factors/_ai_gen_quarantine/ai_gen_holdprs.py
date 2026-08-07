"""AI因子: 持仓时间压力因子 | 置信:70% | 基于价格与移动平均线的偏离程度及波动率，识别价格长期横盘导致超时止损的风险。当价格在均线附近窄幅震荡时，容易触发max_hold_timeout或sl亏损。使用价格偏离度除以ATR，并取负数，窄幅震荡时因子接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holding_Time_Pressure_Factor(BaseFactor):
    """基于价格与移动平均线的偏离程度及波动率，识别价格长期横盘导致超时止损的风险。当价格在均线附近窄幅震荡时，容易触发max_hold_timeout或sl亏损。使用价格偏离度除以ATR，并取负数，窄幅震荡时因子接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdprs",
            name="Holding Time Pressure Factor",
            display_name="持仓时间压力因子",
            description="基于价格与移动平均线的偏离程度及波动率，识别价格长期横盘导致超时止损的风险。当价格在均线附近窄幅震荡时，容易触发max_hold_timeout或sl亏损。使用价格偏离度除以ATR，并取负数，窄幅震荡时因子接近-1。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        close = df['close']
        high = df['high']
        low = df['low']
        ma = close.rolling(20).mean()
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 价格偏离度（百分比）归一化
        deviation = (close - ma) / ma
        # 相对波幅的偏离度
        relative_dev = deviation / (atr / close)  # 用ATR比例衡量偏离显著性
        # 窄幅震荡时relative_dev接近0，取负值表示高风险
        raw = -relative_dev.abs() * 2
        result = raw.clip(-1, 1)
        # 当偏离过大时转为正值（趋势明确，安全）
        result = result.where(relative_dev.abs() < 2, 1.0)  # 偏离超过2倍ATR认为趋势明确
        return result.fillna(0)
