"""AI因子: 量价背离因子 | 置信:65% | 当价格波动率上升但成交量萎缩时，表示市场缺乏流动性或参与者犹豫，容易导致假突破和止损亏损，尤其在未知状态下。通过计算近期波动率与成交量的乖离率，归一化到[-1,1]，正值表示背离风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Volatility_Divergence(BaseFactor):
    """当价格波动率上升但成交量萎缩时，表示市场缺乏流动性或参与者犹豫，容易导致假突破和止损亏损，尤其在未知状态下。通过计算近期波动率与成交量的乖离率，归一化到[-1,1]，正值表示背离风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volvol",
            name="Volume_Volatility_Divergence",
            display_name="量价背离因子",
            description="当价格波动率上升但成交量萎缩时，表示市场缺乏流动性或参与者犹豫，容易导致假突破和止损亏损，尤其在未知状态下。通过计算近期波动率与成交量的乖离率，归一化到[-1,1]，正值表示背离风险高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算收益率序列
        ret = data['close'].pct_change()
        # 波动率：过去20期标准差
        vol = ret.rolling(20, min_periods=10).std()
        # 成交量：过去20期平均成交量
        avg_vol = data['volume'].rolling(20, min_periods=10).mean()
        # 当前成交量相对均值的偏离（归一化）
        vol_ratio = data['volume'] / avg_vol
        # 将波动率也归一化到滚动分位数
        vol_rank = vol.rank(pct=True)
        # 量价背离：高波动但低成交量 => 风险
        divergence = vol_rank * (1 - vol_ratio.clip(0,2)/2)
        # 平滑并映射到[-1,1]
        result = (divergence.rolling(5).mean() - 0.5) * 2
        return result.clip(-1, 1).fillna(0)
