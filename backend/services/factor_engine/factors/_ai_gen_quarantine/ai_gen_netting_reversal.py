"""AI因子: 净持仓反转探测 | 置信:60% | 基于价格与VWAP的偏离和波动率扩张识别可能的净持仓反转。当价格偏离VWAP超过1.5个ATR且ATR最近扩张时，预测价格回归VWAP。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NettingReversalDetector(BaseFactor):
    """基于价格与VWAP的偏离和波动率扩张识别可能的净持仓反转。当价格偏离VWAP超过1.5个ATR且ATR最近扩张时，预测价格回归VWAP。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_netting_reversal",
            name="Netting Reversal Detector",
            display_name="净持仓反转探测",
            description="基于价格与VWAP的偏离和波动率扩张识别可能的净持仓反转。当价格偏离VWAP超过1.5个ATR且ATR最近扩张时，预测价格回归VWAP。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算VWAP (典型价格)
        tp = (data['high'] + data['low'] + data['close']) / 3
        vwap = (tp * data['volume']).rolling(14).sum() / data['volume'].rolling(14).sum()
        # 计算ATR
        tr = pd.concat([data['high'] - data['low'],
                        abs(data['high'] - data['close'].shift(1)),
                        abs(data['low'] - data['close'].shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 偏离度 (单位: ATR)
        deviation = (data['close'] - vwap) / (atr + 1e-10)
        # ATR扩张: 当前ATR > 前5日均值
        atr_ma = atr.rolling(5).mean()
        atr_expanding = atr > atr_ma * 1.1
        # 信号：偏离>1.5且ATR扩张 -> 看跌；偏离<-1.5且ATR扩张 -> 看涨
        signal = np.where((deviation > 1.5) & atr_expanding, -deviation/3.0, 0.0)
        signal = np.where((deviation < -1.5) & atr_expanding, -deviation/3.0, signal)
        result = pd.Series(signal, index=data.index).fillna(0.0)
        return result.clip(-1, 1)
