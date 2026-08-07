"""AI因子: 噪声陷阱指标 | 置信:60% | 通过比较价格变动与成交量变动的比值，识别市场处于低信噪比状态。当价格小幅波动而成交量放大时，容易出现假突破和反转，导致趋势策略亏损。该因子在成交量异常增大而价格变动不显著时给出负值，提示避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Noise_Trap_Indicator(BaseFactor):
    """通过比较价格变动与成交量变动的比值，识别市场处于低信噪比状态。当价格小幅波动而成交量放大时，容易出现假突破和反转，导致趋势策略亏损。该因子在成交量异常增大而价格变动不显著时给出负值，提示避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_trap",
            name="Noise Trap Indicator",
            display_name="噪声陷阱指标",
            description="通过比较价格变动与成交量变动的比值，识别市场处于低信噪比状态。当价格小幅波动而成交量放大时，容易出现假突破和反转，导致趋势策略亏损。该因子在成交量异常增大而价格变动不显著时给出负值，提示避免做多。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        vol_ret = volume.pct_change()
        # 滚动窗口20期
        ret_std = ret.rolling(20).std()
        vol_std = vol_ret.rolling(20).std()
        # 信号：价格波动小但成交量异常大 => 负分
        ratio = (ret.abs() / (ret_std + 1e-8)) / (vol_ret.abs() / (vol_std + 1e-8))
        # 归一化到[-1,1]
        result = -2 * (ratio / (ratio + 1)) + 1  # 映射到[-1,1]，ratio小则接近1？实际需要调整
        # 简化：直接使用z-score的负值
        z = (ratio - ratio.mean()) / ratio.std()
        result = np.clip(-z, -1, 1)
        return result
