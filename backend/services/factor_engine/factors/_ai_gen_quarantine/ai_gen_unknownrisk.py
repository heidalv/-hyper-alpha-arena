"""AI因子: 未知状态风险 | 置信:60% | 复合因子融合价格位置、成交量异常、短期动量混乱度，专门识别日志中'regime=unknown'的形态。当因子为强负值时，做多风险极大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown_Regime_Risk(BaseFactor):
    """复合因子融合价格位置、成交量异常、短期动量混乱度，专门识别日志中'regime=unknown'的形态。当因子为强负值时，做多风险极大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknownrisk",
            name="Unknown Regime Risk",
            display_name="未知状态风险",
            description="复合因子融合价格位置、成交量异常、短期动量混乱度，专门识别日志中'regime=unknown'的形态。当因子为强负值时，做多风险极大。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 1. 价格位置：当前价格在近期区间的位置
        recent_high = high.rolling(20).max()
        recent_low = low.rolling(20).min()
        price_position = (close - recent_low) / (recent_high - recent_low + 1e-10)
        # 中间位置（0.4~0.6）表示不确定性高，做多危险
        mid_zone = ((price_position > 0.35) & (price_position < 0.65)).astype(float)

        # 2. 成交量异常：成交量激增但价格没有突破（窄幅震荡）
        vol_ma20 = volume.rolling(20).mean()
        vol_spike = (volume > vol_ma20 * 1.5).astype(float)
        price_range = (high - low) / close
        range_ma20 = price_range.rolling(20).mean()
        narrow_range = (price_range < range_ma20 * 0.8).astype(float)  # 窄幅

        # 3. 短期动量混乱：价格来回摆动（计算连续方向变化次数）
        direction = np.sign(close.diff())
        direction_change = (direction != direction.shift()).astype(float)
        chaos = direction_change.rolling(5).sum() / 5.0  # 5周期内方向变化比例
        high_chaos = (chaos > 0.6).astype(float)

        # 4. 基本面缺失（模拟）：价格接近但未突破前高前低
        near_high = (close > recent_high * 0.98).astype(float)
        near_low = (close < recent_low * 1.02).astype(float)
        stuck = (near_high | near_low).astype(float)

        # 组合：未知状态风险 = 中间位置 + 窄幅高量 + 高混乱 + 被卡位置
        raw = (mid_zone * 0.3 + vol_spike * narrow_range * 0.3 + high_chaos * 0.2 + stuck * 0.2)
        # 映射到[-1,1]，风险越高越负
        result = -1.0 * raw
        # 平滑
        result = result.rolling(2).mean()
        return result.fillna(0).clip(-1, 1)
