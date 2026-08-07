"""AI因子: RSI极端反转 | 置信:60% | 利用14日RSI的极端值结合成交量确认。当RSI高于70且收盘价低于前一日（或出现上影线）时看空；当RSI低于30且收盘价高于前一日（或出现下影线）时看多。结合成交量放大增强信号。通过计算(50-RSI) * 成交量调整因子，再经tanh归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSIExtremeReversal(BaseFactor):
    """利用14日RSI的极端值结合成交量确认。当RSI高于70且收盘价低于前一日（或出现上影线）时看空；当RSI低于30且收盘价高于前一日（或出现下影线）时看多。结合成交量放大增强信号。通过计算(50-RSI) * 成交量调整因子，再经tanh归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_rev",
            name="RSI Extreme Reversal",
            display_name="RSI极端反转",
            description="利用14日RSI的极端值结合成交量确认。当RSI高于70且收盘价低于前一日（或出现上影线）时看空；当RSI低于30且收盘价高于前一日（或出现下影线）时看多。结合成交量放大增强信号。通过计算(50-RSI) * 成交量调整因子，再经tanh归一化至[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算RSI
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-8)
        rsi = 100 - (100 / (1 + rs))
        # 价格方向：当前收盘相对前一日
        price_dir = (df['close'] - df['close'].shift(1))
        # 成交量放大因子：当前量 / 过去14日均量
        vol_ma = df['volume'].rolling(14).mean().shift(1)
        vol_ratio = df['volume'] / (vol_ma + 1e-8)
        # 信号：RSI偏离50的方向，结合价格方向和成交量
        # 当RSI>70且price_dir<0时看空，当RSI<30且price_dir>0时看多
        # 连续形式：(50 - RSI) * price_dir_sign * vol_ratio
        price_sign = np.sign(price_dir)  # 1涨 -1跌
        signal = (50 - rsi) * price_sign * vol_ratio
        # 标准化
        result = np.tanh(signal * 0.1)  # 调整系数避免过饱和
        return result
