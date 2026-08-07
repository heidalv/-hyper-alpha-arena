"""AI因子: 止损接近度风险 | 置信:60% | 计算当前价格与最近关键支撑阻力位（前10日最高/最低）的距离占ATR的比例。距离越近，越容易被假突破触发止损，输出负信号（高风险）；距离越远，抗冲击越强，输出正信号（低风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossProximityRisk(BaseFactor):
    """计算当前价格与最近关键支撑阻力位（前10日最高/最低）的距离占ATR的比例。距离越近，越容易被假突破触发止损，输出负信号（高风险）；距离越远，抗冲击越强，输出正信号（低风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_proximity",
            name="Stop-Loss Proximity Risk",
            display_name="止损接近度风险",
            description="计算当前价格与最近关键支撑阻力位（前10日最高/最低）的距离占ATR的比例。距离越近，越容易被假突破触发止损，输出负信号（高风险）；距离越远，抗冲击越强，输出正信号（低风险）。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 近期最高和最低
        recent_high = high.rolling(10).max()
        recent_low = low.rolling(10).min()
        # 到关键位的距离（取较小者）
        dist_to_high = (recent_high - close) / (close + 1e-10)
        dist_to_low = (close - recent_low) / (close + 1e-10)
        dist = pd.concat([dist_to_high, dist_to_low], axis=1).min(axis=1)
        # 计算ATR(10)
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(10).mean() / (close + 1e-10)
        # 距离与ATR的比值，越小越危险
        ratio = dist / (atr + 1e-10)
        # 映射到[-1,1]，ratio <1 表示危险
        result = 1.0 - 2.0 * np.exp(-ratio / 0.5)  # ratio=0->-1, ratio=1->0.86
        return result
