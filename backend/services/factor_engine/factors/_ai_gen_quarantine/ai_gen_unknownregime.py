"""AI因子: 未知状态检测因子 | 置信:60% | 从波动率异常、趋势模糊、成交量突变三个维度综合判断当前市场是否处于难以预测的未知状态。当因子值接近-1时，表示高度不确定(regime=unknown)，应避免开仓；接近+1时表示状态清晰。直接对应错误模式中的核心问题。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeDetector(BaseFactor):
    """从波动率异常、趋势模糊、成交量突变三个维度综合判断当前市场是否处于难以预测的未知状态。当因子值接近-1时，表示高度不确定(regime=unknown)，应避免开仓；接近+1时表示状态清晰。直接对应错误模式中的核心问题。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknownregime",
            name="Unknown Regime Detector",
            display_name="未知状态检测因子",
            description="从波动率异常、趋势模糊、成交量突变三个维度综合判断当前市场是否处于难以预测的未知状态。当因子值接近-1时，表示高度不确定(regime=unknown)，应避免开仓；接近+1时表示状态清晰。直接对应错误模式中的核心问题。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 波动率异常
        returns = close.pct_change()
        vol_short = returns.rolling(10).std()
        vol_long = returns.rolling(50).std().replace(0, np.nan)
        vol_ratio = (vol_short / vol_long).fillna(1)
        vol_score = np.tanh((vol_ratio - 1) * 5)  # -1高波动异常
        # 趋势模糊度：使用ADX简化版
        high = data['high']
        low = data['low']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 方向移动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
        minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move
        plus_di = 100 * (plus_dm.rolling(14).sum() / atr)
        minus_di = 100 * (minus_dm.rolling(14).sum() / atr)
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.rolling(14).mean().fillna(25)
        trend_score = np.tanh((adx - 25) / 15)  # 小于25为弱趋势(-1),大于25为强趋势(+1)
        # 成交量突变
        vol_ma = volume.rolling(20).mean().replace(0, np.nan)
        vol_spike = (volume / vol_ma).fillna(1)
        vol_score = np.tanh((vol_spike - 1) * 2)  # 成交量激增为+1? 但异常放量可能表示未知，取反
        vol_anomaly = -vol_score  # 放量异常为-1
        # 综合：权重分配
        result = (vol_score * 0.4 + trend_score * 0.4 + vol_anomaly * 0.2)
        return result.fillna(0).clip(-1, 1)
