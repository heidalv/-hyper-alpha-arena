"""AI因子: 量价背离因子 | 置信:60% | 发现价格与成交量走势的不一致：当价格创近期新高但成交量萎缩时，可能为假突破或流动性不足，容易引发亏损。计算最近10根K线价格变化方向与成交量变化方向的相关系数负值作为信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume-Price Divergence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_div", name="Volume-Price Divergence",
        display_name="量价背离因子", description="发现价格与成交量走势的不一致：当价格创近期新高但成交量萎缩时，可能为假突破或流动性不足，容易引发亏损。计算最近10根K线价格变化方向与成交量变化方向的相关系数负值作为信号。",
        category="composite", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    window = 10
    price_change = data['close'].diff(window)
    vol_change = data['volume'].diff(window)
    # 滚动相关系数
    corr = price_change.rolling(window).corr(vol_change)
    # 当相关系数为正（同向）且价格上升时，认为正常；负相关则背离，信号为负
    # 将corr映射到[-1,1]，直接取-corr     signal = -corr.fillna(0)
    return signal.clip(-1,1)
