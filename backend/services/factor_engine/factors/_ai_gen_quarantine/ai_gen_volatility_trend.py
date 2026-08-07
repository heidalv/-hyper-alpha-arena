"""AI因子: 波动率调整趋势脆弱性 | 置信:55% | 衡量趋势的脆弱程度，当趋势强度低而波动率高时，市场易出现反转。使用ADX与ATR的比率，结合趋势方向，输出[-1,1]信号，正值表示趋势脆弱易反转向上，负值表示反转向下。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedTrendFragility(BaseFactor):
    """衡量趋势的脆弱程度，当趋势强度低而波动率高时，市场易出现反转。使用ADX与ATR的比率，结合趋势方向，输出[-1,1]信号，正值表示趋势脆弱易反转向上，负值表示反转向下。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_trend",
            name="Volatility-Adjusted Trend Fragility",
            display_name="波动率调整趋势脆弱性",
            description="衡量趋势的脆弱程度，当趋势强度低而波动率高时，市场易出现反转。使用ADX与ATR的比率，结合趋势方向，输出[-1,1]信号，正值表示趋势脆弱易反转向上，负值表示反转向下。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ADX（简化版）
        high = data['high']
        low = data['low']
        close = data['close']
        # 方向移动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        # 正方向移动
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 真实波幅
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        # 平滑
        atr14 = tr.rolling(14).mean()
        plus_di14 = pd.Series(plus_dm).rolling(14).mean() / atr14 * 100
        minus_di14 = pd.Series(minus_dm).rolling(14).mean() / atr14 * 100
        # ADX
        dx = (plus_di14 - minus_di14).abs() / (plus_di14 + minus_di14) * 100
        adx = dx.rolling(14).mean()
        # 趋势脆弱性：低ADX高ATR
        fragility = 1 - (adx / 100) * (1 / (atr14 / atr14.mean() + 1e-10))
        fragility = fragility.clip(-1, 1)
        # 方向：若close在20日均线上方，则脆弱性为正（可能反转下跌？但我们要双向）
        # 用价格位置调整
        trend_dir = (close - close.rolling(20).mean()) / close.rolling(20).std()
        signal = -np.sign(trend_dir) * fragility
        return signal
