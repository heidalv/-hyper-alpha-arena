"""AI因子: 流动性异常检测 | 置信:40% | 通过价格变化与成交量的关系识别流动性不足或异常交易行为。当价量背离（价格下跌但缩量，或上涨放量但后续无力）时，容易导致止损或超时亏损，输出负值；价量配合良好时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Anomaly_Detector(BaseFactor):
    """通过价格变化与成交量的关系识别流动性不足或异常交易行为。当价量背离（价格下跌但缩量，或上涨放量但后续无力）时，容易导致止损或超时亏损，输出负值；价量配合良好时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liqabn",
            name="Liquidity Anomaly Detector",
            display_name="流动性异常检测",
            description="通过价格变化与成交量的关系识别流动性不足或异常交易行为。当价量背离（价格下跌但缩量，或上涨放量但后续无力）时，容易导致止损或超时亏损，输出负值；价量配合良好时输出正值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算价格变化率（1期）
        ret = close.pct_change()
        # 成交量变化率（1期）
        vol_change = volume.pct_change()
        # 定义价量关系得分
        # 上涨时量增为佳（正相关），下跌时量缩为佳（负相关）
        # 理想情况：ret>0时vol_change>0，ret<0时vol_change<0
        ideal_sign = np.sign(ret) * np.sign(vol_change)
        # 异常情况：上涨缩量或下跌放量（ideal_sign=-1）
        anomaly = (ideal_sign == -1).astype(float)
        # 再加上成交量异常放大（超过近期均值3倍）作为额外风险
        vol_ma20 = volume.rolling(20).mean()
        vol_spike = (volume > vol_ma20 * 3).astype(float) * 0.5
        # 综合得分：正常情况+1，异常-1
        score = 1.0 - 2.0 * (anomaly + vol_spike).clip(0, 1)
        score = score.fillna(0)
        return pd.Series(score, index=data.index)
