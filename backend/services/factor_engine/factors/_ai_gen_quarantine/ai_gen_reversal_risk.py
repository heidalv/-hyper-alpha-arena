"""AI因子: 反转风险指标 | 置信:55% | 结合布林带偏离度与RSI识别潜在的极端位置。当价格偏离布林带上下轨较远且RSI进入超买/超卖区（>70或<30）时，认为反转风险高，输出反向信号；若处于中轨附近且RSI中性，输出接近0，提示市场状态未知，避免交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalRiskIndicator(BaseFactor):
    """结合布林带偏离度与RSI识别潜在的极端位置。当价格偏离布林带上下轨较远且RSI进入超买/超卖区（>70或<30）时，认为反转风险高，输出反向信号；若处于中轨附近且RSI中性，输出接近0，提示市场状态未知，避免交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_risk",
            name="Reversal Risk Indicator",
            display_name="反转风险指标",
            description="结合布林带偏离度与RSI识别潜在的极端位置。当价格偏离布林带上下轨较远且RSI进入超买/超卖区（>70或<30）时，认为反转风险高，输出反向信号；若处于中轨附近且RSI中性，输出接近0，提示市场状态未知，避免交易。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 布林带参数
        bb_period = 20
        bb_std = 2
        # RSI参数
        rsi_period = 14
        # 计算布林带
        sma = close.rolling(bb_period).mean()
        std = close.rolling(bb_period).std()
        upper = sma + bb_std * std
        lower = sma - bb_std * std
        # 偏离度归一化到[-1,1]： (close - sma) / (2*bb_std*std) 近似
        deviation = (close - sma) / (bb_std * std)
        deviation = deviation.clip(-1, 1)
        # 计算RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(rsi_period).mean()
        avg_loss = loss.rolling(rsi_period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 超买超卖信号
        overbought = (rsi > 70).astype(float)
        oversold = (rsi < 30).astype(float)
        # 反转信号：当价格在布林带极端且RSI极端时，输出反向
        # 卖出信号：价格>上轨且超买 -> 向下反转 -> 负值
        sell_signal = (close > upper) & (rsi > 70)
        # 买入信号：价格<下轨且超卖 -> 向上反转 -> 正值
        buy_signal = (close < lower) & (rsi < 30)
        # 组合：基础信号是 -deviation（均值回归方向），但只在极端条件下激活
        base = -deviation * 0.5  # 一般偏向回归
        # 极端情况下强化信号
        extreme = np.where(sell_signal, -0.9, np.where(buy_signal, 0.9, 0))
        # 当布林带宽度很窄（震荡）或RSI中性时，输出接近0
        bandwidth = (upper - lower) / sma
        narrow = bandwidth < bandwidth.rolling(60).mean() * 0.5
        neutral_rsi = (rsi > 40) & (rsi < 60)
        weight = np.where(narrow & neutral_rsi, 0.1, 1.0)
        result = (base + extreme) * weight
        result = result.clip(-1, 1).fillna(0)
        return result
