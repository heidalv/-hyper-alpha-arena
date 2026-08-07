"""AI因子: 持仓时间动量衰减 | 置信:60% | 捕捉趋势后期动量衰减的信号：计算过去5周期价格变化率与过去50周期价格变化率的比值，同时结合成交量变化率（过去5日平均成交量除以过去20日平均成交量）的倒数。当短期动量强于长期但成交量萎缩时，因子接近-1（提示超时风险）；反之接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeMomentum(BaseFactor):
    """捕捉趋势后期动量衰减的信号：计算过去5周期价格变化率与过去50周期价格变化率的比值，同时结合成交量变化率（过去5日平均成交量除以过去20日平均成交量）的倒数。当短期动量强于长期但成交量萎缩时，因子接近-1（提示超时风险）；反之接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_mom",
            name="HoldTimeMomentum",
            display_name="持仓时间动量衰减",
            description="捕捉趋势后期动量衰减的信号：计算过去5周期价格变化率与过去50周期价格变化率的比值，同时结合成交量变化率（过去5日平均成交量除以过去20日平均成交量）的倒数。当短期动量强于长期但成交量萎缩时，因子接近-1（提示超时风险）；反之接近+1。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 短期动量（5日收益率）
        ret5 = close.pct_change(5)
        # 长期动量（50日收益率）
        ret50 = close.pct_change(50)
        # 动量比
        mom_ratio = ret5 / (ret50.abs() + 1e-8)
        # 成交量相对变化：5日均量 / 20日均量
        vol5 = volume.rolling(5).mean()
        vol20 = volume.rolling(20).mean()
        vol_ratio = vol5 / (vol20 + 1e-8)
        # 组合：动量比高但成交量萎缩时信号偏空
        combo = mom_ratio - 1.0 / (vol_ratio + 1e-8)
        # 标准化到[-1,1]
        result = np.tanh(combo * 0.5)
        return result
