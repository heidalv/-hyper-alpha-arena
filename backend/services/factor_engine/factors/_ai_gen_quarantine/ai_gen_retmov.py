"""AI因子: 收益动量反转因子 | 置信:55% | 衡量短期收益序列的自相关性，正值表示正向动量（趋势延续），负值表示反转倾向（均值回归）。通过计算过去5日收益率与过去5-10日收益率的相关性，并归一化至[-1,1]。在regime=unknown时帮助判断趋势持续性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Returnmomentum(BaseFactor):
    """衡量短期收益序列的自相关性，正值表示正向动量（趋势延续），负值表示反转倾向（均值回归）。通过计算过去5日收益率与过去5-10日收益率的相关性，并归一化至[-1,1]。在regime=unknown时帮助判断趋势持续性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_retmov",
            name="ReturnMomentum",
            display_name="收益动量反转因子",
            description="衡量短期收益序列的自相关性，正值表示正向动量（趋势延续），负值表示反转倾向（均值回归）。通过计算过去5日收益率与过去5-10日收益率的相关性，并归一化至[-1,1]。在regime=unknown时帮助判断趋势持续性。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        import pandas as pd
        close = data['close']
        ret = close.pct_change()
        # 计算过去5日累计收益
        ret5 = ret.rolling(5).sum()
        # 计算过去5日至10日累计收益（滞后5天）
        ret5_lag = ret.rolling(5).sum().shift(5)
        # 滚动20日相关系数
        corr = ret5.rolling(20).corr(ret5_lag)
        # 使用tanh归一化
        result = np.tanh(corr * 2)
        result = result.fillna(0).clip(-1, 1)
        return result
