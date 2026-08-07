"""AI因子: 市场状态规避因子 | 置信:60% | 通过比较价格波动率和趋势一致性，识别类似于'unknown'的模糊状态。当波动率异常低但价格无明显趋势时给出负向信号避免交易，否则根据趋势给出信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Avoid(BaseFactor):
    """通过比较价格波动率和趋势一致性，识别类似于'unknown'的模糊状态。当波动率异常低但价格无明显趋势时给出负向信号避免交易，否则根据趋势给出信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regimeavoid",
            name="Regime_Avoid",
            display_name="市场状态规避因子",
            description="通过比较价格波动率和趋势一致性，识别类似于'unknown'的模糊状态。当波动率异常低但价格无明显趋势时给出负向信号避免交易，否则根据趋势给出信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算20日波动率（标准差）
        vol = data['close'].pct_change().rolling(20).std()
        # 计算趋势强度：价格相对于60日均线的位置
        ma60 = data['close'].rolling(60).mean()
        dist = (data['close'] - ma60) / ma60
        # 计算波动率的分位数
        vol_percentile = vol.rank(pct=True)
        # 当波动率处于低位（<0.3分位）且价格距离均线较近（<1%）时认为是unknown状态
        unknown_cond = (vol_percentile < 0.3) & (abs(dist) < 0.01)
        # 非未知状态：使用趋势信号（dist归一化）
        trend_signal = dist.clip(-0.1, 0.1) * 10  # 缩放到[-1,1]
        # 未知状态：信号置0（避免交易）
        result = np.where(unknown_cond, 0.0, trend_signal)
        return pd.Series(result, index=data.index)
