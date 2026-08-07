"""AI因子: 多时间框架动量一致性因子 | 置信:60% | 在不同时间尺度（短、中、长）上计算动量方向，并衡量其一致性。当各周期方向一致时给出正信号，相反或混乱时给出负信号。适用于过滤掉regime=unknown时的虚假趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multitimemomentumconsistency(BaseFactor):
    """在不同时间尺度（短、中、长）上计算动量方向，并衡量其一致性。当各周期方向一致时给出正信号，相反或混乱时给出负信号。适用于过滤掉regime=unknown时的虚假趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momcon",
            name="MultiTimeMomentumConsistency",
            display_name="多时间框架动量一致性因子",
            description="在不同时间尺度（短、中、长）上计算动量方向，并衡量其一致性。当各周期方向一致时给出正信号，相反或混乱时给出负信号。适用于过滤掉regime=unknown时的虚假趋势。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 定义三个时间尺度：5, 20, 60周期
        mom5 = close - close.shift(5)
        mom20 = close - close.shift(20)
        mom60 = close - close.shift(60)
        # 方向：1为上涨，-1为下跌
        dir5 = pd.Series(np.sign(mom5), index=data.index)
        dir20 = pd.Series(np.sign(mom20), index=data.index)
        dir60 = pd.Series(np.sign(mom60), index=data.index)
        # 一致性得分：三个方向相同为1，两个相同为0.5，完全不同为0
        sum_dir = dir5 + dir20 + dir60
        # 当三个绝对值均为1时，和为3或-3 -> 一致性1；两个一致时和为2或-2 -> 0.5；其他为0
        consistency = np.where((sum_dir.abs() == 3), 1.0,
                               np.where((sum_dir.abs() == 2), 0.5, 0.0))
        # 乘以整体方向符号（上升为+，下降为-），得到[-1,1]
        overall_dir = np.sign(mom5 + mom20 + mom60).replace(0, 0)
        result = consistency * overall_dir
        return pd.Series(result, index=data.index).fillna(0)
