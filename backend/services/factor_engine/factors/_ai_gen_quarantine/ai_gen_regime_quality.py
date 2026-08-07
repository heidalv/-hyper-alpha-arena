"""AI因子: 市场状态质量因子 | 置信:70% | 通过ADX（平均趋向指数）和ATR（平均真实波幅）的相对变化，判断当前市场是否具有清晰趋势或高波动性。当ADX低于25且ATR较低时，认为市场处于'unknown'状态，因子输出负值；反之，趋势明确或高波动时输出正值。旨在规避未知市场机制下的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeQualityIndicator(BaseFactor):
    """通过ADX（平均趋向指数）和ATR（平均真实波幅）的相对变化，判断当前市场是否具有清晰趋势或高波动性。当ADX低于25且ATR较低时，认为市场处于'unknown'状态，因子输出负值；反之，趋势明确或高波动时输出正值。旨在规避未知市场机制下的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_quality",
            name="Regime Quality Indicator",
            display_name="市场状态质量因子",
            description="通过ADX（平均趋向指数）和ATR（平均真实波幅）的相对变化，判断当前市场是否具有清晰趋势或高波动性。当ADX低于25且ATR较低时，认为市场处于'unknown'状态，因子输出负值；反之，趋势明确或高波动时输出正值。旨在规避未知市场机制下的亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算最高价与最低价的差
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算TR和ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 计算ADX
        # 先计算+DM和-DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑DM
        plus_dm_smooth = pd.Series(plus_dm).rolling(14).mean()
        minus_dm_smooth = pd.Series(minus_dm).rolling(14).mean()
        # 计算DI
        plus_di = 100 * plus_dm_smooth / atr
        minus_di = 100 * minus_dm_smooth / atr
        # DX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 计算波动率调整因子
        vol_ratio = atr / close.rolling(20).mean() * 100
        # 组合：当ADX<25且vol_ratio<2%时，认为未知状态，输出-1；否则映射到[-1,1]
        # 使用线性映射
        adx_norm = (adx - 25) / 25  # 假设adx范围0-50，中心25
        vol_norm = (vol_ratio - 2) / 3  # 假设正常范围0-5%
        combined = 0.6 * adx_norm + 0.4 * vol_norm
        result = np.clip(combined, -0.999, 0.999)
        # 对于缺失值填充0
        result = result.fillna(0)
        return result
