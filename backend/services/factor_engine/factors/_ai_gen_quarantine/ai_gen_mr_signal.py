"""AI因子: 均值回复信号 | 置信:65% | 基于日内价格在高低点之间的位置，结合短期波动率，判断是否处于过度延伸状态。当价格接近近期高点且波动率较低时给出负值（警惕反转），反之给出正值。用于捕捉因过度追涨导致的亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Signal(BaseFactor):
    """基于日内价格在高低点之间的位置，结合短期波动率，判断是否处于过度延伸状态。当价格接近近期高点且波动率较低时给出负值（警惕反转），反之给出正值。用于捕捉因过度追涨导致的亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr_signal",
            name="Mean Reversion Signal",
            display_name="均值回复信号",
            description="基于日内价格在高低点之间的位置，结合短期波动率，判断是否处于过度延伸状态。当价格接近近期高点且波动率较低时给出负值（警惕反转），反之给出正值。用于捕捉因过度追涨导致的亏损模式。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算过去5日最高最低
        high5 = data['high'].rolling(5).max()
        low5 = data['low'].rolling(5).min()
        # 当前价格在区间内的位置 [0,1]
        pos = (data['close'] - low5) / (high5 - low5).replace(0, np.nan)
        # 计算ATR20作为波动率
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        # 归一化波动率 (最近5日ATR均值 / 20日ATR均值)
        atr5 = tr.rolling(5).mean()
        vol_ratio = atr5 / atr20.replace(0, np.nan)
        # 综合：当位置极端（>0.8或<0.2）且波动率低时，信号强。用(-1,1)映射：位置0.5处为0
        # 设计：sig = (0.5 - pos) * vol_ratio 再缩放
        raw = (0.5 - pos) * vol_ratio
        # 用tanh限制范围
        result = np.tanh(raw * 3)  # 乘3增强灵敏度
        return result
