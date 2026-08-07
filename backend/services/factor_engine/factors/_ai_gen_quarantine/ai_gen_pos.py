"""AI因子: 区间位置因子 | 置信:70% | 计算当前收盘价在过去N日（默认20）最高最低之间的百分位，当百分位接近0.5时表示价格处于区间中间，无明确方向，容易产生震荡亏损；因子值映射为(0.5-百分位)*2，使得中间区域为0，两端为±1，但这里我们设计为正值表示高风险（中间区域），负值表示低风险。使用反转映射：风险=1 - 2*|百分位-0.5|，即越接近0.5风险越高（值接近1），越接近两端风险越低（值接近-1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Position in Range(BaseFactor):
    """计算当前收盘价在过去N日（默认20）最高最低之间的百分位，当百分位接近0.5时表示价格处于区间中间，无明确方向，容易产生震荡亏损；因子值映射为(0.5-百分位)*2，使得中间区域为0，两端为±1，但这里我们设计为正值表示高风险（中间区域），负值表示低风险。使用反转映射：风险=1 - 2*|百分位-0.5|，即越接近0.5风险越高（值接近1），越接近两端风险越低（值接近-1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pos",
            name="Position in Range",
            display_name="区间位置因子",
            description="计算当前收盘价在过去N日（默认20）最高最低之间的百分位，当百分位接近0.5时表示价格处于区间中间，无明确方向，容易产生震荡亏损；因子值映射为(0.5-百分位)*2，使得中间区域为0，两端为±1，但这里我们设计为正值表示高风险（中间区域），负值表示低风险。使用反转映射：风险=1 - 2*|百分位-0.5|，即越接近0.5风险越高（值接近1），越接近两端风险越低（值接近-1）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            period = 20
            high_20 = data['high'].rolling(period).max()
            low_20 = data['low'].rolling(period).min()
            pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-10)
            # 当pos=0.5时风险最大(1)，当pos=0或1时风险最小(-1)
            risk = 1 - 2 * np.abs(pos - 0.5)
            return risk.fillna(0).clip(-1,1)
