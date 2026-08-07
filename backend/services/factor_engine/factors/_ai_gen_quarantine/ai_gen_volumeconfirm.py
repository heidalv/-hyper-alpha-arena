"""AI因子: 成交量确认因子 | 置信:55% | 计算价格变化与成交量变化的相关系数（滚动20期），当价格上升但成交量萎缩时，相关系数为负，提示虚假突破风险，因子值为负，不利于做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Confirmation_Factor(BaseFactor):
    """计算价格变化与成交量变化的相关系数（滚动20期），当价格上升但成交量萎缩时，相关系数为负，提示虚假突破风险，因子值为负，不利于做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumeconfirm",
            name="Volume Confirmation Factor",
            display_name="成交量确认因子",
            description="计算价格变化与成交量变化的相关系数（滚动20期），当价格上升但成交量萎缩时，相关系数为负，提示虚假突破风险，因子值为负，不利于做多。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        volume = data['volume'].values
        # 价格变化率
        price_chg = np.diff(close) / close[:-1]
        price_chg = np.append(np.nan, price_chg)
        # 成交量变化率
        vol_chg = np.diff(volume) / (volume[:-1] + 1e-10)
        vol_chg = np.append(np.nan, vol_chg)
        # 滚动相关系数
        corr = pd.Series(price_chg).rolling(20, min_periods=20).corr(pd.Series(vol_chg)).values
        # 当相关系数为负时，价格与成交量背离，因子为负，取-corr映射到[-1,1]
        result = -np.nan_to_num(corr, nan=0.0)
        return pd.Series(result, index=data.index)
