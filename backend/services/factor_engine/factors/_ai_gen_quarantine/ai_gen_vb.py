"""AI因子: 波动率突破假象 | 置信:60% | 在低波动环境下，价格尝试向上突破但动能不足，容易引发假突破导致做多亏损。因子计算近期ATR与价格的比值（波动率），并比较当前收盘价与过去N日最高点的距离。若波动率低且收盘价接近高点但未创新高，则输出负值（看空），否则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Breakout_Fakeout(BaseFactor):
    """在低波动环境下，价格尝试向上突破但动能不足，容易引发假突破导致做多亏损。因子计算近期ATR与价格的比值（波动率），并比较当前收盘价与过去N日最高点的距离。若波动率低且收盘价接近高点但未创新高，则输出负值（看空），否则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vb",
            name="Volatility Breakout Fakeout",
            display_name="波动率突破假象",
            description="在低波动环境下，价格尝试向上突破但动能不足，容易引发假突破导致做多亏损。因子计算近期ATR与价格的比值（波动率），并比较当前收盘价与过去N日最高点的距离。若波动率低且收盘价接近高点但未创新高，则输出负值（看空），否则输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        period = 14
        # 计算ATR
        tr = pd.DataFrame({
            'hl': df['high'] - df['low'],
            'hc': abs(df['high'] - df['close'].shift()),
            'lc': abs(df['low'] - df['close'].shift())
        }).max(axis=1)
        atr = tr.rolling(period).mean()
        # 波动率比率 (ATR/价格)
        vol_ratio = atr / df['close']
        # 计算价格相对于过去N日最高点的位置
        high_n = df['high'].rolling(period).max()
        dist_to_high = (df['close'] - high_n) / (df['high'].rolling(period).max() - df['low'].rolling(period).min() + 1e-10)
        # 波动率低且价格接近高点（但未突破）的复合信号
        low_vol = (vol_ratio < vol_ratio.rolling(50).quantile(0.3)).astype(int)
        near_high = (dist_to_high > -0.05) & (dist_to_high < 0.0)
        raw = -(low_vol * near_high).astype(float)
        # 用tanh归一化到[-1,1]
        result = pd.Series(np.tanh(raw * 5), index=df.index).fillna(0)
        return result
