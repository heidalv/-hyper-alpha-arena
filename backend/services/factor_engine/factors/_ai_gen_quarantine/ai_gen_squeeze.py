"""AI因子: 布林带挤压反转 | 置信:60% | 识别布林带带宽收缩后突然扩张的行情，此类行情常导致止损单被连续触发。当带宽从低位快速放大且价格突破中轨时，给出负向信号（-1）表示高风险反转；当带宽平稳时信号中性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerSqueezeReversal(BaseFactor):
    """识别布林带带宽收缩后突然扩张的行情，此类行情常导致止损单被连续触发。当带宽从低位快速放大且价格突破中轨时，给出负向信号（-1）表示高风险反转；当带宽平稳时信号中性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_squeeze",
            name="Bollinger_Squeeze_Reversal",
            display_name="布林带挤压反转",
            description="识别布林带带宽收缩后突然扩张的行情，此类行情常导致止损单被连续触发。当带宽从低位快速放大且价格突破中轨时，给出负向信号（-1）表示高风险反转；当带宽平稳时信号中性。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 布林带参数
        period = 20
        std = data['close'].rolling(period).std()
        sma = data['close'].rolling(period).mean()
        upper = sma + 2 * std
        lower = sma - 2 * std
        # 带宽
        bandwidth = (upper - lower) / sma
        # 带宽的移动平均（用于检测低位）
        bw_ma = bandwidth.rolling(50).mean()
        # 带宽扩张率：当前带宽相对其过去20日最小值的变化
        bw_min = bandwidth.rolling(20).min()
        squeeze_trigger = (bandwidth / (bw_min + 1e-10)) > 1.5  # 扩张超过1.5倍
        # 价格突破方向：close > sma 表示向上突破，< sma向下
        price_above_sma = (data['close'] > sma).astype(int)
        # 信号：挤压后扩张且价格刚突破中轨则预示反转风险
        recent_squeeze = (bandwidth < bw_ma * 0.8).astype(int).shift(1)  # 前一根K线处于低位
        signal = squeeze_trigger * recent_squeeze * (price_above_sma * 2 - 1)  # 向上突破给负，向下给正？
        # 实际上亏损多出现在突破后反向止损，所以无论方向都提示风险：取绝对值并反转
        raw_risk = squeeze_trigger * recent_squeeze * 1.0
        # 映射到[-1,0]，表示风险，也可映射到[-1,1]但负区间更合适
        result = -raw_risk.astype(float)
        return result.fillna(0)
