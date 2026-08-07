"""AI因子: 多空失衡 | 置信:60% | 基于日内价格行为度量多头和空头的力量对比。使用(收盘价-开盘价)/(最高价-最低价)的滚动标准差调整，当收盘接近最低时空头强，但若连续多根K线空头占优后出现减弱信号，则可能反转。正值表示空头力量正在衰竭（潜在反转看多），负值表示空头持续强势。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Bull-Bear Imbalance(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_bull_bear_balance", name="Bull-Bear Imbalance",
        display_name="多空失衡", description="基于日内价格行为度量多头和空头的力量对比。使用(收盘价-开盘价)/(最高价-最低价)的滚动标准差调整，当收盘接近最低时空头强，但若连续多根K线空头占优后出现减弱信号，则可能反转。正值表示空头力量正在衰竭（潜在反转看多），负值表示空头持续强势。",
        category="composite", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    open = data['open']
    high = data['high']
    low = data['low']
    close = data['close']
    # 日内位置：0~1，靠近0为空头主导，靠近1为多头主导
    position = (close - low) / (high - low + 1e-10)
    # 滚动均值，观察趋势变化
    n = 14
    ma = position.rolling(window=n).mean()
    # 最近K根位置与均值的偏离，负值表示空头更弱（低于均值）
    raw = (position - ma) / (position.rolling(window=n).std() + 1e-10)
    # 取相反数：当position从低位回升（raw为正）时，空头衰竭
    result = raw * (-1)
    result = result.fillna(0).clip(-1, 1)
    return result
