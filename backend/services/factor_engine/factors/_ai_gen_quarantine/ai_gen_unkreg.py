"""AI因子: 未知状态识别 | 置信:50% | 基于历史波动率与成交量的异常偏离，识别市场进入未知状态的概率。当波动率与成交量同时出现极端值（高或低）时，因子值趋近于1（高风险未知状态），反之为-1（正常状态）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Detection(BaseFactor):
    """基于历史波动率与成交量的异常偏离，识别市场进入未知状态的概率。当波动率与成交量同时出现极端值（高或低）时，因子值趋近于1（高风险未知状态），反之为-1（正常状态）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkreg",
            name="Unknown Regime Detection",
            display_name="未知状态识别",
            description="基于历史波动率与成交量的异常偏离，识别市场进入未知状态的概率。当波动率与成交量同时出现极端值（高或低）时，因子值趋近于1（高风险未知状态），反之为-1（正常状态）。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算日波动率：最高最低价差/收盘价
        volatility = (data['high'] - data['low']) / data['close']
        # 计算成交量变化率
        vol_change = data['volume'].pct_change()
        # 滚动20日标准差
        vol_std = volatility.rolling(20).std()
        vol_mean = volatility.rolling(20).mean()
        # 标准化波动率偏离
        vol_z = (volatility - vol_mean) / vol_std.replace(0, np.nan)
        # 成交量变化率滚动20日标准差
        volc_std = vol_change.rolling(20).std()
        volc_mean = vol_change.rolling(20).mean()
        volc_z = (vol_change - volc_mean) / volc_std.replace(0, np.nan)
        # 合成：取绝对值大的那个，并压缩到[-1,1]
        combined = np.maximum(np.abs(vol_z.fillna(0)), np.abs(volc_z.fillna(0)))
        # 使用tanh平滑
        result = np.tanh(combined * 0.5)
        return pd.Series(result, index=data.index).fillna(0)
