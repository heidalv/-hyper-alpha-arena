"""AI因子: 动量衰减因子 | 置信:60% | 衡量近期动量衰减的速度。通过比较短期收益率和长期收益率的比值，并结合成交量的变化，识别动量快速衰减（即趋势动能衰竭）的行情。此时若做多持有，容易因反转或超时导致亏损，输出负值以提示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Decay_Factor(BaseFactor):
    """衡量近期动量衰减的速度。通过比较短期收益率和长期收益率的比值，并结合成交量的变化，识别动量快速衰减（即趋势动能衰竭）的行情。此时若做多持有，容易因反转或超时导致亏损，输出负值以提示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_dec",
            name="Momentum Decay Factor",
            display_name="动量衰减因子",
            description="衡量近期动量衰减的速度。通过比较短期收益率和长期收益率的比值，并结合成交量的变化，识别动量快速衰减（即趋势动能衰竭）的行情。此时若做多持有，容易因反转或超时导致亏损，输出负值以提示风险。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        close = data['close']
        volume = data['volume']

        # 短期动量：过去5日收益率
        ret_short = close.pct_change(5)
        # 长期动量：过去30日收益率
        ret_long = close.pct_change(30)
        # 动量衰减指标：短期收益与长期收益的比值（衰减 => 比值下降）
        # 若长期收益为正但短期收益转负，则衰减明显
        momentum_ratio = ret_short / (ret_long + 1e-10)

        # 成交量变化：过去5日平均成交量 vs 过去30日平均成交量
        vol_short = volume.rolling(5).mean()
        vol_long = volume.rolling(30).mean()
        vol_ratio = vol_short / (vol_long + 1e-10)

        # 动量衰减信号：当短期收益低于长期收益（比值 < 1）且成交量放大（vol_ratio > 1.2）时，认为动能衰竭
        decay_signal = np.where((momentum_ratio < 1.0) & (vol_ratio > 1.2), -1.0, 0.0)
        # 累积平滑
        result = pd.Series(decay_signal, index=data.index).rolling(3).mean().fillna(0)
        return result
