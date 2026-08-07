"""AI因子: 趋势置信度 | 置信:60% | 结合ADX和ATR识别强趋势与震荡环境。当趋势强度低且波动率高时，容易在未知状态下频繁止损。因子值越高表示趋势越明确，适合趋势跟踪；越低表示震荡，应避免持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConfidence(BaseFactor):
    """结合ADX和ATR识别强趋势与震荡环境。当趋势强度低且波动率高时，容易在未知状态下频繁止损。因子值越高表示趋势越明确，适合趋势跟踪；越低表示震荡，应避免持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trdconf",
            name="TrendConfidence",
            display_name="趋势置信度",
            description="结合ADX和ATR识别强趋势与震荡环境。当趋势强度低且波动率高时，容易在未知状态下频繁止损。因子值越高表示趋势越明确，适合趋势跟踪；越低表示震荡，应避免持仓。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns open, high, low, close, volume
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ATR
        period = 14
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 计算ADX
        up = high.diff()
        down = -low.diff()
        plus_dm = pd.DataFrame(np.where((up > down) & (up > 0), up, 0), index=close.index)
        minus_dm = pd.DataFrame(np.where((down > up) & (down > 0), down, 0), index=close.index)
        tr_smooth = tr.rolling(period).sum()
        plus_di = 100 * plus_dm.rolling(period).sum() / tr_smooth
        minus_di = 100 * minus_dm.rolling(period).sum() / tr_smooth
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 标准化ADX至[0,1]，然后映射到[-1,1]
        adx_norm = adx / 100.0  # ADX range 0-100
        # 波动率因子：ATR相对于价格的百分比，越高越表明震荡可能
        volatility = atr / close * 100
        vol_norm = volatility / volatility.rolling(100).max().fillna(volatility.max())  # 归一化到[0,1]
        # 综合：趋势置信度 = adx_norm * (1 - vol_norm) ，再映射到[-1,1]
        temp = adx_norm * (1 - vol_norm)
        # 使用tanh映射到[-1,1]并平滑
        result = pd.Series(np.tanh(3 * (temp - 0.5)), index=close.index)
        result = result.fillna(0).clip(-1, 1)
        return result
