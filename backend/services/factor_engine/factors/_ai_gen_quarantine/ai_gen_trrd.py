"""AI因子: 趋势状态风险检测 | 置信:55% | 通过ADX和价格动量计算趋势强度，结合持仓超时风险。亏损模式中多次出现max_hold_timeout和master_running，暗示趋势不明确时持仓过久导致亏损。该因子在ADX低于20（无趋势）且近期价格波动率上升时输出负值（看空），规避做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Regime_Risk_Detector(BaseFactor):
    """通过ADX和价格动量计算趋势强度，结合持仓超时风险。亏损模式中多次出现max_hold_timeout和master_running，暗示趋势不明确时持仓过久导致亏损。该因子在ADX低于20（无趋势）且近期价格波动率上升时输出负值（看空），规避做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trrd",
            name="Trend Regime Risk Detector",
            display_name="趋势状态风险检测",
            description="通过ADX和价格动量计算趋势强度，结合持仓超时风险。亏损模式中多次出现max_hold_timeout和master_running，暗示趋势不明确时持仓过久导致亏损。该因子在ADX低于20（无趋势）且近期价格波动率上升时输出负值（看空），规避做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ADX
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * plus_dm.rolling(14).mean() / atr
        minus_di = 100 * minus_dm.rolling(14).mean() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-8)
        adx = dx.rolling(14).mean()
        # 计算20日波动率变化
        vol = data['close'].pct_change().rolling(20).std()
        vol_change = vol.diff(5)
        # 无趋势(adx<20)且波动率上升时，风险高，做多易亏损，输出负值
        factor = np.where(adx < 20, -np.clip(vol_change * 100, -1, 1), 0)
        # 趋势强烈时，让因子为正，但根据亏损全做多，可略降低
        factor = np.where(adx > 25, np.clip(adx / 100, 0, 0.5), factor)
        return factor.fillna(0)
