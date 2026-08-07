"""AI因子: 持仓风险评分 | 置信:70% | 基于价格在近期高低点中的位置以及成交量异常变化，评估当前开仓是否容易触发止损。例如价格处于近期低点附近时做空风险大，处于高点附近时做多风险大。正值表示风险较低（适合开仓），负值表示高止损风险区。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Position Risk Score(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_position_risk", name="Position Risk Score",
        display_name="持仓风险评分", description="基于价格在近期高低点中的位置以及成交量异常变化，评估当前开仓是否容易触发止损。例如价格处于近期低点附近时做空风险大，处于高点附近时做多风险大。正值表示风险较低（适合开仓），负值表示高止损风险区。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    # 近期20周期的高低点
    recent_high = high.rolling(20).max()
    recent_low = low.rolling(20).min()
    # 价格在区间中的位置，0~1之间
    pos = (close - recent_low) / (recent_high - recent_low + 1e-10)
    # 远离边界（中间区域）则风险低，靠近边界风险高
    risk_curve = 1 - 2 * (pos - 0.5).abs()  # 中间为1，边界为0
    # 成交量异常：如果近期成交量突然放大且价格突破边界，风险更高
    vol_ratio = volume / volume.rolling(10).mean()
    # 检测突破
    breakout_up = (close > recent_high.shift(1)) & (vol_ratio > 1.5)
    breakout_down = (close < recent_low.shift(1)) & (vol_ratio > 1.5)
    # 突破时降低分数
    risk_score = risk_curve
    risk_score[breakout_up] = risk_score[breakout_up] * 0.3
    risk_score[breakout_down] = risk_score[breakout_down] * 0.3
    # 映射到[-1,1]，中间为正，边界为负
    result = 2 * risk_score - 1
    return result
