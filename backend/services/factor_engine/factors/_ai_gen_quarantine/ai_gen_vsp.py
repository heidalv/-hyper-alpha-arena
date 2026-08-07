"""AI因子: 波动率突变 | 置信:70% | 检测短期波动率相对长期波动率的突变，突变时容易触发止损（sl）和master_running_close_tiny。输出负值表示波动率飙升风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class volatility_spike(BaseFactor):
    """检测短期波动率相对长期波动率的突变，突变时容易触发止损（sl）和master_running_close_tiny。输出负值表示波动率飙升风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vsp",
            name="volatility_spike",
            display_name="波动率突变",
            description="检测短期波动率相对长期波动率的突变，突变时容易触发止损（sl）和master_running_close_tiny。输出负值表示波动率飙升风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算对数收益率
        ret = np.log(close / close.shift(1))
        # 短期波动率：5日标准差，年化（这里只需相对量）
        short_vol = ret.rolling(5).std()
        # 长期波动率：20日标准差
        long_vol = ret.rolling(20).std()
        # 比率，越大表示波动率突变
        ratio = short_vol / (long_vol + 1e-10)
        # 当ratio > 1.5时开始有风险，映射到[-1,0]
        raw = -((ratio - 1.5) / 1.5).clip(0, 1)  # 超过3倍则-1
        result = raw.clip(-1, 0)
        return result.fillna(0)
