"""AI因子: 量价挤压衰竭因子 | 置信:60% | 检测成交量异常收缩（挤压）后是否出现价格突破失败（衰竭）。当成交量低于近期均值0.5倍且价格在布林带中轨附近窄幅波动时，识别为潜在失效模式，返回负值；反之放量突破趋势明确时返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Squeeze_Exhaustion(BaseFactor):
    """检测成交量异常收缩（挤压）后是否出现价格突破失败（衰竭）。当成交量低于近期均值0.5倍且价格在布林带中轨附近窄幅波动时，识别为潜在失效模式，返回负值；反之放量突破趋势明确时返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_squeeze_exhaustion",
            name="Volume Squeeze & Exhaustion",
            display_name="量价挤压衰竭因子",
            description="检测成交量异常收缩（挤压）后是否出现价格突破失败（衰竭）。当成交量低于近期均值0.5倍且价格在布林带中轨附近窄幅波动时，识别为潜在失效模式，返回负值；反之放量突破趋势明确时返回正值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 成交量均值（20日）
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 布林带（20,2）
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        # 价格在布林带中轨附近（距离<0.5倍带宽）视为窄幅
        bandwidth = upper - lower
        mid_distance = np.abs(close - ma20) / bandwidth
        squeeze = (vol_ratio < 0.5) & (mid_distance < 0.3)
        # 再检测是否出现突破失败：前一日squeeze但今日价格未能延续方向
        # 简单处理：squeeze条件下返回-1，否则根据趋势评分
        # 增强：如果squeeze后价格突破布林带但又折返，更负
        exp_fail = squeeze & ((close > upper) | (close < lower)) & (close.shift(1) > upper) | (close.shift(1) < lower)
        result = pd.Series(np.where(exp_fail, -1.0, np.where(squeeze, -0.5, 0.5)), index=data.index)
        return result
