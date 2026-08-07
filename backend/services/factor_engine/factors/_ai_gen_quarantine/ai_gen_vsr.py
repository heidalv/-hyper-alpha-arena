"""AI因子: 波动率稳定性比 | 置信:50% | 计算短期波动率（5日ATR）与长期波动率（20日ATR）的比值。当比值接近1时，市场可能处于无明确方向的震荡，因子接近-1；当比值显著偏离1（过高或过低）时，可能处于趋势或极端波动，因子接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Stability_Ratio(BaseFactor):
    """计算短期波动率（5日ATR）与长期波动率（20日ATR）的比值。当比值接近1时，市场可能处于无明确方向的震荡，因子接近-1；当比值显著偏离1（过高或过低）时，可能处于趋势或极端波动，因子接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vsr",
            name="Volatility_Stability_Ratio",
            display_name="波动率稳定性比",
            description="计算短期波动率（5日ATR）与长期波动率（20日ATR）的比值。当比值接近1时，市场可能处于无明确方向的震荡，因子接近-1；当比值显著偏离1（过高或过低）时，可能处于趋势或极端波动，因子接近+1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 真实波幅TR
        tr = np.maximum((high - low).abs(), np.maximum((high - close.shift()).abs(), (low - close.shift()).abs()))
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        ratio = atr_short / atr_long
        # 当ratio接近1时，表示短期波动与长期一致，可能处于无趋势震荡
        # 归一化到[-1,1]：偏离1越远，值越大（正或负？但需注意方向）
        # 但我们要避免未知状态，所以ratio接近1给-1，偏离给+1。
        # 可以用对数或绝对值：偏离度 = |ratio - 1|，然后映射到[-1,1]但需要符号？
        # 只关心偏离程度，不关心方向，所以直接让因子与偏离度负相关
        deviation = (ratio - 1.0).abs()
        # 最大偏离假设为1（即ratio=0或2），但实际上可能更大，需要截断
        deviation = deviation.clip(0, 1.0)
        # 因子 = 1 - 2*deviation，即当deviation=0时=1，deviation=1时=-1
        factor = 1.0 - 2.0 * deviation
        # 但注意：ratio可能非常大，deviation>1时因子恒为-1，可以接受
        return pd.Series(factor.clip(-1,1), index=data.index)
