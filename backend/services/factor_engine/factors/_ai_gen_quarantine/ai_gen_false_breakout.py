"""AI因子: 假突破检测器 | 置信:55% | 利用布林带识别假突破后快速反转。当价格突破上轨/下轨后立即回落到带内，视为假突破。输出+1表示空头陷阱（下破后回升，看涨反转），-1表示多头陷阱（上破后回落，看跌反转）。用于预警master_running_close可能因假突破造成的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FalseBreakoutDetector(BaseFactor):
    """利用布林带识别假突破后快速反转。当价格突破上轨/下轨后立即回落到带内，视为假突破。输出+1表示空头陷阱（下破后回升，看涨反转），-1表示多头陷阱（上破后回落，看跌反转）。用于预警master_running_close可能因假突破造成的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_false_breakout",
            name="False Breakout Detector",
            display_name="假突破检测器",
            description="利用布林带识别假突破后快速反转。当价格突破上轨/下轨后立即回落到带内，视为假突破。输出+1表示空头陷阱（下破后回升，看涨反转），-1表示多头陷阱（上破后回落，看跌反转）。用于预警master_running_close可能因假突破造成的亏损。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 布林带参数
        period = 20
        k = 2.0
    
        close = data['close']
    
        # 中轨、标准差
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = ma + k * std
        lower = ma - k * std
    
        # 突破标记：当前K线是否突破（收盘价）
        break_up = close > upper
        break_down = close < lower
    
        # 前一根K线是否也突破（用于识别刚突破）
        prev_break_up = break_up.shift(1)
        prev_break_down = break_down.shift(1)
    
        # 当前回到带内
        inside_now = (close <= upper) & (close >= lower)
    
        # 假突破信号：前一K线突破，当前回到带内
        false_up = prev_break_up & inside_now   # 多头陷阱
        false_down = prev_break_down & inside_now  # 空头陷阱
    
        # 方向：多头陷阱输出-1，空头陷阱输出+1
        raw = false_down.astype(int) * 1 + false_up.astype(int) * (-1)
    
        # 信号可能较稀疏，轻微平滑并保持限幅
        result = raw.rolling(2).mean().fillna(0).clip(-1, 1)
        return result
