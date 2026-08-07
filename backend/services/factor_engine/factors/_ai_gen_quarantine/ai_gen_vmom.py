"""AI因子: 波动率调整动量因子 | 置信:60% | 将动量信号除以波动率，得到类似夏普比率的指标，用于识别趋势的可靠性。高波动时降低动量权重，避免在剧烈震荡中追涨杀跌导致止损。计算过去20日的收益率均值和标准差，取比值后使用反正切函数归一化到[-1,1]。正值表示趋势向上且稳定，负值表示趋势向下且稳定，接近0表示趋势不明。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """将动量信号除以波动率，得到类似夏普比率的指标，用于识别趋势的可靠性。高波动时降低动量权重，避免在剧烈震荡中追涨杀跌导致止损。计算过去20日的收益率均值和标准差，取比值后使用反正切函数归一化到[-1,1]。正值表示趋势向上且稳定，负值表示趋势向下且稳定，接近0表示趋势不明。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vmom",
            name="Volatility-Adjusted Momentum",
            display_name="波动率调整动量因子",
            description="将动量信号除以波动率，得到类似夏普比率的指标，用于识别趋势的可靠性。高波动时降低动量权重，避免在剧烈震荡中追涨杀跌导致止损。计算过去20日的收益率均值和标准差，取比值后使用反正切函数归一化到[-1,1]。正值表示趋势向上且稳定，负值表示趋势向下且稳定，接近0表示趋势不明。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算对数收益率
        log_ret = np.log(data['close'] / data['close'].shift(1))
        # 滚动20日均值和标准差
        mean_ret = log_ret.rolling(20).mean()
        std_ret = log_ret.rolling(20).std() + 1e-12
        # 波动率调整动量 = mean / std  (类似Sharpe但未年化)
        raw_sr = mean_ret / std_ret
        # 使用tanh或arctan映射到[-1,1]
        # 用np.arctan乘以2/pi 得到[-1,1]
        result = 2 * np.arctan(raw_sr) / np.pi
        # 处理缺失值
        return result.fillna(0.0)
