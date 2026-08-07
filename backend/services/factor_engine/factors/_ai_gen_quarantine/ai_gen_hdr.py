"""AI因子: 持仓时间风险因子 | 置信:60% | 基于价格路径的累积波动与时间的关系，量化长时间持仓的风险。当价格在一定周期内反复穿越均线、累计波动大但方向收益小，可能预示着持仓超时亏损。通过计算20日内价格的累积绝对移动距离与最终净变化的比值。值域[-1,1]，正表示高风险，负表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldingDurationRiskFactor(BaseFactor):
    """基于价格路径的累积波动与时间的关系，量化长时间持仓的风险。当价格在一定周期内反复穿越均线、累计波动大但方向收益小，可能预示着持仓超时亏损。通过计算20日内价格的累积绝对移动距离与最终净变化的比值。值域[-1,1]，正表示高风险，负表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hdr",
            name="HoldingDurationRiskFactor",
            display_name="持仓时间风险因子",
            description="基于价格路径的累积波动与时间的关系，量化长时间持仓的风险。当价格在一定周期内反复穿越均线、累计波动大但方向收益小，可能预示着持仓超时亏损。通过计算20日内价格的累积绝对移动距离与最终净变化的比值。值域[-1,1]，正表示高风险，负表示低风险。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算20日的价格路径累积绝对变化（总行程）
        abs_chg = close.diff().abs()
        total_path = abs_chg.rolling(20).sum()
        # 计算20日的净变化绝对值
        net_chg = (close - close.shift(20)).abs()
        # 路径效率 = net_chg / total_path，效率越低（接近0）说明路径曲折，持仓风险高
        efficiency = net_chg / (total_path + 1e-10)
        # 反转为风险：风险 = 1 - efficiency，然后压缩到[-1,1]，正数代表高风险
        risk = 1 - efficiency
        result = np.tanh((risk - 0.5) * 4)  # 0.5作为中等风险阈值
        return result.fillna(0)
