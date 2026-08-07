"""AI因子: 空头挤压反转指数 | 置信:60% | 计算价格相对于前N小时最低点的偏离程度，结合成交量放大比例，评估空头挤压风险。当价格快速下跌后出现反弹且成交量激增，通常预示短空挤压，应避免继续做空。因子值域[-1,1]，正值表示空头挤压风险高，负值表示无挤压风险。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Short Squeeze Reversal Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_rev_ss", name="Short Squeeze Reversal Index",
        display_name="空头挤压反转指数", description="计算价格相对于前N小时最低点的偏离程度，结合成交量放大比例，评估空头挤压风险。当价格快速下跌后出现反弹且成交量激增，通常预示短空挤压，应避免继续做空。因子值域[-1,1]，正值表示空头挤压风险高，负值表示无挤压风险。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 参数
    lookback = 10
    vol_lookback = 5
    # 最低价滚动最小值
    low_min = data['low'].rolling(lookback, min_periods=1).min()
    # 价格偏离度 (close - low_min) / low_min
    deviation = (data['close'] - low_min) / (low_min + 1e-10)
    # 成交量变化率：当前成交量相对于前vol_lookback均值
    volume_ma = data['volume'].rolling(vol_lookback, min_periods=1).mean()
    vol_ratio = data['volume'] / (volume_ma + 1e-10)
    # 组合：偏离度与成交量放大乘积，再调整到[-1,1]
    raw = deviation * vol_ratio
    # 归一化到[-1,1]：使用tanh或clip
    result = np.tanh(raw * 0.5)
    result = result.fillna(0)
    return pd.Series(result, index=data.index)
