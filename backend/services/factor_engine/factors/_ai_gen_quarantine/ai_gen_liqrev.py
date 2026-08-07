"""AI因子: 流动性磁铁反转 | 置信:65% | 检测价格快速突破近期极值后迅速反转的形态。当价格创出N日新高但收盘价低于开盘价（阴线），或创出N日新低但收盘价高于开盘价（阳线）时，产生强反转信号。使用ATR标准化后映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """检测价格快速突破近期极值后迅速反转的形态。当价格创出N日新高但收盘价低于开盘价（阴线），或创出N日新低但收盘价高于开盘价（阳线）时，产生强反转信号。使用ATR标准化后映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liqrev",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转",
            description="检测价格快速突破近期极值后迅速反转的形态。当价格创出N日新高但收盘价低于开盘价（阴线），或创出N日新低但收盘价高于开盘价（阳线）时，产生强反转信号。使用ATR标准化后映射到[-1,1]。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        n = 10
        atr_period = 14
        # 计算ATR
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(atr_period).mean()
        # 计算N日高低点
        hh = high.rolling(n).max()
        ll = low.rolling(n).min()
        # 判断突破并反转
        # 新高但收盘低于开盘（阴线） 或 新低但收盘高于开盘（阳线）
        cond_new_high = (high == hh) & (close < data['open'])
        cond_new_low = (low == ll) & (close > data['open'])
        # 反转强度：用收盘与极值的距离归一化
        strength = pd.Series(0.0, index=data.index)
        strength[cond_new_high] = -(close[cond_new_high] - ll[cond_new_high]) / (hh[cond_new_high] - ll[cond_new_high] + 1e-10)
        strength[cond_new_low] = (close[cond_new_low] - ll[cond_new_low]) / (hh[cond_new_low] - ll[cond_new_low] + 1e-10)
        # 用ATR标准化并限制范围
        result = strength / (atr / close + 0.01)
        result = np.clip(result, -1, 1)
        return result
