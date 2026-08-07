"""AI因子: 趋势模式不确定性 | 置信:60% | 模拟错误模式中"regime=unknown"的状态，当市场无明显趋势（如价格在短期均线和长期均线之间纠缠，ADX低于阈值）时，做多容易亏损。计算趋势强度指标并反转，值越接近1表示趋势确定性越高（适合趋势策略），越接近-1表示混沌状态（应避免做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Regime_Uncertainty(BaseFactor):
    """模拟错误模式中"regime=unknown"的状态，当市场无明显趋势（如价格在短期均线和长期均线之间纠缠，ADX低于阈值）时，做多容易亏损。计算趋势强度指标并反转，值越接近1表示趋势确定性越高（适合趋势策略），越接近-1表示混沌状态（应避免做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regimeunknown",
            name="Trend Regime Uncertainty",
            display_name="趋势模式不确定性",
            description="模拟错误模式中'regime=unknown'的状态，当市场无明显趋势（如价格在短期均线和长期均线之间纠缠，ADX低于阈值）时，做多容易亏损。计算趋势强度指标并反转，值越接近1表示趋势确定性越高（适合趋势策略），越接近-1表示混沌状态（应避免做多）。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']

        # 计算ADX
        period = 14
        # +DM, -DM
        high_diff = high.diff()
        low_diff = low.diff()
        plus_dm = ((high_diff > 0) & (high_diff > -low_diff)).astype(int) * high_diff
        minus_dm = ((low_diff > 0) & (-low_diff > high_diff)).astype(int) * (-low_diff)
        # TR
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(period).sum()
        plus_di = 100 * plus_dm.fillna(0).rolling(period).sum() / atr
        minus_di = 100 * minus_dm.fillna(0).rolling(period).sum() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 均线缠绕：短期均线（5）和长期均线（20）的交叉状态
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        diff_ma = (ma5 - ma20) / close
        # 综合不确定性：低ADX + 均线接近
        low_adx = (adx < 25).astype(int)
        tight_ma = (abs(diff_ma) < 0.01).astype(int)
        uncertain = low_adx | tight_ma
        # 当不确定性高时给予负向信号（做多风险）
        result = -uncertain.astype(float)
        # 加上趋势方向加权：如果均线死叉则更负
        death_cross = ((ma5 < ma20) & (ma5.shift(1) > ma20.shift(1))).astype(int)
        result -= death_cross * 0.5
        result = result.clip(-1, 1)
        return result
