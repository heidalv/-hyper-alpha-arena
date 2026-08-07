"""AI因子: 波动率挤压指标 | 置信:60% | 检测价格波动率在低位长时间盘整后突然扩大的情况，此类位置易产生假突破或快速反转。指标负值越大表示当前处于高风险挤压状态，应避免开仓；正值为正常波动环境。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Squeeze Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volatility_squeeze", name="Volatility Squeeze Indicator",
        display_name="波动率挤压指标", description="检测价格波动率在低位长时间盘整后突然扩大的情况，此类位置易产生假突破或快速反转。指标负值越大表示当前处于高风险挤压状态，应避免开仓；正值为正常波动环境。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    high = data['high']
    low = data['low']
    close = data['close']
    # 计算ATR（14周期）
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # 计算ATR的短期波动率（5日标准差）
    atr_std = atr.rolling(5).std()
    # 波动率挤压程度：当前ATR相对于近期ATR均值的偏离，除以标准差
    atr_ma = atr.rolling(20).mean()
    squeeze = (atr - atr_ma) / (atr_std + 1e-10)
    # 另外加入价格区间宽度检查
    range_5 = (high.rolling(5).max() - low.rolling(5).min()) / close.rolling(5).mean()
    range_ma = range_5.rolling(20).mean()
    range_squeeze = (range_5 - range_ma) / (range_5.rolling(20).std() + 1e-10)
    # 综合：squeeze负值表示波动收缩，突然转正可能假突破；这里我们直接映射到[-1,1]
    combined = squeeze * 0.6 + range_squeeze * 0.4
    result = combined.clip(-3, 3) / 3
    result = result * -1  # 负值表示高风险挤压（避免开仓）
    return result
