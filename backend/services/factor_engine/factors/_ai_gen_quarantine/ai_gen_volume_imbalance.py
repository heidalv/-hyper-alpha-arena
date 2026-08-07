"""AI因子: 成交量不平衡反转 | 置信:60% | 基于成交量与价格方向的不匹配来预测反转。当价格上涨但成交量萎缩（多头衰竭）或价格下跌但成交量萎缩（空头衰竭）时，反转概率增加。因子计算价格方向与成交量变化率的乘积，并取负值作为反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumeimbalancereversal(BaseFactor):
    """基于成交量与价格方向的不匹配来预测反转。当价格上涨但成交量萎缩（多头衰竭）或价格下跌但成交量萎缩（空头衰竭）时，反转概率增加。因子计算价格方向与成交量变化率的乘积，并取负值作为反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_imbalance",
            name="VolumeImbalanceReversal",
            display_name="成交量不平衡反转",
            description="基于成交量与价格方向的不匹配来预测反转。当价格上涨但成交量萎缩（多头衰竭）或价格下跌但成交量萎缩（空头衰竭）时，反转概率增加。因子计算价格方向与成交量变化率的乘积，并取负值作为反转信号。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 价格方向（1上涨，-1下跌）
        price_dir = np.sign(data['close'] - data['close'].shift(1))
        # 成交量变化率
        vol_change = data['volume'].pct_change(3)
        # 组合：价格方向与成交量变化反向 => 反转信号
        factor = -price_dir * vol_change
        # 平滑处理
        factor = factor.rolling(2).mean().fillna(0)
        factor = np.clip(factor, -1, 1)
        return factor
