"""AI因子: 动量分歧复合 | 置信:70% | 捕捉短期和长期动量的方向不一致，同时结合成交量确认。当短期动量向上但长期动量向下且成交量萎缩，预示反转风险；做空时易被反弹止损。返回[-1,1]，负值建议做多，正值建议做空。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Momentum Divergence Composite(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_mdc", name="Momentum Divergence Composite",
        display_name="动量分歧复合", description="捕捉短期和长期动量的方向不一致，同时结合成交量确认。当短期动量向上但长期动量向下且成交量萎缩，预示反转风险；做空时易被反弹止损。返回[-1,1]，负值建议做多，正值建议做空。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    volume = data['volume']
    # 短期动量(3周期)
    short_ret = close.pct_change(3)
    # 长期动量(20周期)
    long_ret = close.pct_change(20)
    # 动量分歧: 短期和长期符号相反时强度
    div = -np.sign(short_ret) * np.sign(long_ret) * (short_ret.abs() + long_ret.abs()) / 2
    # 成交量确认: 价格变动与成交量背离（缩量反弹）
    vol_ma = volume.rolling(10).mean()
    vol_ratio = volume / vol_ma.replace(0, np.nan)
    # 当短期上涨但成交量低于均值时加强负值
    cond = (short_ret > 0) & (vol_ratio < 0.8)
    signal = np.where(cond, -np.clip(div + 0.3, -1, 1), div)
    # 短期下跌且放量时加强正值
    cond2 = (short_ret < 0) & (vol_ratio > 1.2)
    signal = np.where(cond2, np.clip(div + 0.3, -1, 1), signal)
    return pd.Series(np.clip(signal, -1, 1), index=data.index).fillna(0)
