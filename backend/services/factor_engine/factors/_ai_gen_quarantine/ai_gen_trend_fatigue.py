"""AI因子: 趋势衰竭检测 | 置信:65% | ADX从高位回落且价格波动率收缩，表示原有趋势动能衰竭，容易出现持仓超时亏损。信号负值表示趋势衰竭应谨慎交易，正值表示趋势健康。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendFatigueDetector(BaseFactor):
    """ADX从高位回落且价格波动率收缩，表示原有趋势动能衰竭，容易出现持仓超时亏损。信号负值表示趋势衰竭应谨慎交易，正值表示趋势健康。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_fatigue",
            name="Trend Fatigue Detector",
            display_name="趋势衰竭检测",
            description="ADX从高位回落且价格波动率收缩，表示原有趋势动能衰竭，容易出现持仓超时亏损。信号负值表示趋势衰竭应谨慎交易，正值表示趋势健康。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # 计算 True Range 和方向运动
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(window=period).mean()
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * pd.Series(plus_dm, index=data.index).rolling(window=period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=data.index).rolling(window=period).mean() / atr
        dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        adx = dx.rolling(window=period).mean()
        # ADX变化率
        adx_change = adx.diff(5)
        # 波动率收缩：布林带宽度变化
        bb_width = (close.rolling(window=20).mean() + 2*close.rolling(window=20).std()) - (close.rolling(window=20).mean() - 2*close.rolling(window=20).std())
        bb_width_change = bb_width.diff(5)
        # 合成信号：ADX下降且布林带收窄 -> 负值，趋势衰竭
        raw = np.where(adx_change < 0, -1, 1) * np.where(bb_width_change < 0, 1.5, 0.7)
        raw = np.clip(raw, -2, 2)
        # 标准化到[-1,1]
        result = pd.Series(raw, index=data.index).rolling(window=10).mean() / 2.0
        result = result.clip(-1, 1)
        return result
