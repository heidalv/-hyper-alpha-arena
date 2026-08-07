"""AI因子: 短期RSI反转信号 | 置信:60% | 检测超买超卖后的快速反转：计算14周期RSI，当RSI从极端区域（>70或<30）快速回落到中性区域时，可能预示趋势衰竭，适合反向交易。信号在RSI>70且随后下降时给出负向，RSI<30且随后上升时给出正向。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Short-term RSI Reversal(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_rsi_rev", name="Short-term RSI Reversal",
        display_name="短期RSI反转信号", description="检测超买超卖后的快速反转：计算14周期RSI，当RSI从极端区域（>70或<30）快速回落到中性区域时，可能预示趋势衰竭，适合反向交易。信号在RSI>70且随后下降时给出负向，RSI<30且随后上升时给出正向。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    delta = data['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    # 计算RSI变化
    rsi_change = rsi.diff()
    # 信号：当RSI>70且下降时 -> 负信号；当RSI<30且上升时 -> 正信号
    signal = pd.Series(0, index=data.index)
    signal[(rsi > 70) & (rsi_change < 0)] = -1
    signal[(rsi < 30) & (rsi_change > 0)] = 1
    return signal.fillna(0)
