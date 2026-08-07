"""AI因子: 价格盘整形态因子 | 置信:55% | 通过检测连续小实体K线（蜡烛实体占波幅比例小）以及价格在近期区间内的位置，识别盘整末期可能的假突破。当出现多根小实体且价格位于区间中间时，因子接近-1（风险区域）；若有大实体突破则接近+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Price Consolidation Pattern(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_price_consolidation", name="Price Consolidation Pattern",
        display_name="价格盘整形态因子", description="通过检测连续小实体K线（蜡烛实体占波幅比例小）以及价格在近期区间内的位置，识别盘整末期可能的假突破。当出现多根小实体且价格位于区间中间时，因子接近-1（风险区域）；若有大实体突破则接近+1。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    df = data.copy()
    open = df['open']
    high = df['high']
    low = df['low']
    close = df['close']
    
    # 实体大小与波幅比率
    body = (close - open).abs()
    range = high - low
    body_ratio = body / (range + 1e-10)
    
    # 过去N根K线中小实体比例 (阈值：实体<0.3*range)
    small_body = (body_ratio < 0.3).astype(float)
    small_body_count = small_body.rolling(10).sum()
    
    # 价格在近期区间中的位置（10日高低）
    recent_high = high.rolling(10).max()
    recent_low = low.rolling(10).min()
    position = (close - recent_low) / (recent_high - recent_low + 1e-10)
    position_mid = (position - 0.5).abs()  # 偏离中间的程度，0表示正中
    
    # 当小实体数量多且价格靠近中间时，盘整概率高，因子为负
    consol_score = small_body_count / 10.0  # 0-1
    mid_score = 1 - position_mid * 2  # 0-1，正中时为1
    factor = -(consol_score * mid_score)  # 负值表示盘整风险
    # 同时考虑近期是否有大实体突破：若最新K线实体较大且价格突破区间，则正信号
    recent_breakout = ((close > recent_high.shift()) | (close < recent_low.shift())).astype(float)
    breakout_score = body_ratio * recent_breakout  # 大实体突破时为正
    factor = factor.clip(-1, 0) + breakout_score.clip(0, 1)  # 盘整部分负，突破部分正
    factor = factor.clip(-1, 1)
    return factor
