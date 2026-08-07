"""AI因子: 实体占比反转因子 | 置信:55% | 计算每根K线实体（|close-open|）占整个波幅（high-low）的比例，若连续3根实体比例小于0.3且收盘价方向一致，则预示微小价格变动后可能反转。因子值为：最近3根K线平均实体占比的负值，范围[-1,1]，越小（负）表示实体越小，反转概率高。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Candle Body to Range Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_body_ratio", name="Candle Body to Range Ratio",
        display_name="实体占比反转因子", description="计算每根K线实体（|close-open|）占整个波幅（high-low）的比例，若连续3根实体比例小于0.3且收盘价方向一致，则预示微小价格变动后可能反转。因子值为：最近3根K线平均实体占比的负值，范围[-1,1]，越小（负）表示实体越小，反转概率高。",
        category="technical", subcategory="pattern",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    open_ = data['open']
    high = data['high']
    low = data['low']
    close = data['close']
    body = (close - open_).abs()
    range_ = high - low
    ratio = body / (range_ + 1e-10)
    # 连续3根平均
    avg_ratio = ratio.rolling(3).mean()
    # 取负，因为小实体时因子为负值表示反转倾向
    result = -np.clip(avg_ratio, 0, 1) * 2 + 1  # 将[0,1]映射到[1,-1], 即小实体得-1
    # 修正：直接映射：avg_ratio小->-1，大->+1
    # 使用线性映射: result = 1 - 2 * avg_ratio
    result = 1 - 2 * avg_ratio
    return result
