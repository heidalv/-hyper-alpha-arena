"""AI因子: 成交量确认缺失因子 | 置信:60% | 识别价格变动缺乏成交量配合的虚假突破或无序波动。比较最近5日价格变动方向与成交量变化方向的一致性，若两者方向不同且成交量显著萎缩，则判断为不确定性高的状态，给出负信号；若量价同步且放大，则给出正信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Confirmation_Deficit(BaseFactor):
    """识别价格变动缺乏成交量配合的虚假突破或无序波动。比较最近5日价格变动方向与成交量变化方向的一致性，若两者方向不同且成交量显著萎缩，则判断为不确定性高的状态，给出负信号；若量价同步且放大，则给出正信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_conf",
            name="Volume Confirmation Deficit",
            display_name="成交量确认缺失因子",
            description="识别价格变动缺乏成交量配合的虚假突破或无序波动。比较最近5日价格变动方向与成交量变化方向的一致性，若两者方向不同且成交量显著萎缩，则判断为不确定性高的状态，给出负信号；若量价同步且放大，则给出正信号。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        close = df['close']
        volume = df['volume']
        # 5日价格变化方向（1/-1/0）
        price_change = close.diff(5).fillna(0)
        price_dir = np.sign(price_change)
        # 5日成交量变化方向
        vol_change = volume.diff(5).fillna(0)
        vol_dir = np.sign(vol_change)
        # 成交量相对20日均值的比例
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma20 + 1e-10)
        # 量价背离：价格方向与成交量方向相反，或价格有变化但成交量萎缩
        divergence = (price_dir != 0) & (vol_dir != price_dir)
        low_vol = vol_ratio < 0.8
        # 不确定性信号：背离且低量
        uncertain = divergence & low_vol
        # 强确认：同向且量放大
        confirm = (price_dir == vol_dir) & (vol_ratio > 1.2) & (price_dir != 0)
        signal = np.where(uncertain, -1.0,
                          np.where(confirm, 1.0, 0.0))
        result = pd.Series(signal, index=df.index).fillna(0)
        return result.clip(-1,1)
