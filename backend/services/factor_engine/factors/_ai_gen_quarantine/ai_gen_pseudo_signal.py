"""AI因子: 伪信号检测因子 | 置信:60% | 通过价格与20日均线偏离度除以ATR来识别价格纠缠状态。当偏离度绝对值小于0.3时，认为价格在均线附近反复，容易产生假突破，输出负值；否则根据偏离度方向输出[-1,1]的正向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PseudoSignalDetector(BaseFactor):
    """通过价格与20日均线偏离度除以ATR来识别价格纠缠状态。当偏离度绝对值小于0.3时，认为价格在均线附近反复，容易产生假突破，输出负值；否则根据偏离度方向输出[-1,1]的正向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pseudo_signal",
            name="Pseudo Signal Detector",
            display_name="伪信号检测因子",
            description="通过价格与20日均线偏离度除以ATR来识别价格纠缠状态。当偏离度绝对值小于0.3时，认为价格在均线附近反复，容易产生假突破，输出负值；否则根据偏离度方向输出[-1,1]的正向信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 20日均线
        ma20 = close.rolling(20).mean()
        # 偏离度
        dev = (close - ma20) / (atr + 1e-10)
        # 若偏离度绝对值小于0.3则视为纠缠，输出负值
        result = pd.Series(index=data.index, dtype=float)
        # 先填充默认值：根据偏离度正负映射到[-0.5,0.5]但受纠缠影响
        entangled = abs(dev) < 0.3
        result[entangled] = -1.0
        # 非纠缠区域：正向偏离时取正，负向偏离时取负，幅度受限于1
        not_entangled = ~entangled & dev.notna()
        result[not_entangled] = np.clip(dev[not_entangled], -1.0, 1.0)
        result = result.fillna(0.0)
        return result
