"""AI因子: 多时间框架一致性 | 置信:60% | 比较短期（5日）和长期（20日）移动平均线（SMA）的方向是否一致，用于判断市场是否处于明确趋势中。两者同向（均上涨或均下跌）时为+1，反向时为-1，震荡时接近0。利用SMA的差分符号计算。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiFrameAgreement(BaseFactor):
    """比较短期（5日）和长期（20日）移动平均线（SMA）的方向是否一致，用于判断市场是否处于明确趋势中。两者同向（均上涨或均下跌）时为+1，反向时为-1，震荡时接近0。利用SMA的差分符号计算。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mfa",
            name="Multi_Frame_Agreement",
            display_name="多时间框架一致性",
            description="比较短期（5日）和长期（20日）移动平均线（SMA）的方向是否一致，用于判断市场是否处于明确趋势中。两者同向（均上涨或均下跌）时为+1，反向时为-1，震荡时接近0。利用SMA的差分符号计算。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        sma5 = close.rolling(5).mean()
        sma20 = close.rolling(20).mean()
        # 短期方向
        short_dir = np.sign(sma5.diff())
        long_dir = np.sign(sma20.diff())
        # 一致性：两者同号得+1，异号得-1，若其中一个为0则取另一个
        agreement = np.where(short_dir == long_dir, short_dir, -short_dir)
        # 当SMA方向缺乏时（即无法判断），用0代替
        agreement = pd.Series(agreement).fillna(0)
        # 平滑处理？直接用sign映射
        result = agreement.astype(float)
        return result.fillna(0).clip(-1, 1)
