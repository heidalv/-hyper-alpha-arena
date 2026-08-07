"""AI因子: 成交量异常因子 | 置信:60% | 检测成交量是否出现异常放大（超过历史均值3倍以上）且价格无显著同向变动，暗示流动性或参与度异常，给予负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeSpikeAnomaly(BaseFactor):
    """检测成交量是否出现异常放大（超过历史均值3倍以上）且价格无显著同向变动，暗示流动性或参与度异常，给予负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volu",
            name="Volume Spike Anomaly",
            display_name="成交量异常因子",
            description="检测成交量是否出现异常放大（超过历史均值3倍以上）且价格无显著同向变动，暗示流动性或参与度异常，给予负向信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        volume = data['volume']
        close = data['close']
        # 成交量移动均值
        vol_ma = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std()
        # 成交量异常：当前 > 均值 + 3倍标准差
        spike = (volume > vol_ma + 3 * vol_std).astype(float)
        # 价格变动率
        pct_change = close.pct_change()
        # 若成交量异常且价格变化方向不明确（|pct_change|<0.5%）或与成交量方向背离，则视为异常
        price_abs = np.abs(pct_change)
        condition = (spike == 1) & (price_abs < 0.005)
        factor = pd.Series(np.where(condition, -1.0, 1.0), index=data.index)
        return factor
