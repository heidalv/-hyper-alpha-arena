"""AI因子: 成交量异常均值回归 | 置信:50% | 结合价格偏离移动平均线的程度与成交量异常放大（尤其是突破后缩量），预测短期反转。正值预示回调（做空），负值预示反弹（做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionVolumeAnomaly(BaseFactor):
    """结合价格偏离移动平均线的程度与成交量异常放大（尤其是突破后缩量），预测短期反转。正值预示回调（做空），负值预示反弹（做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr_vol",
            name="Mean Reversion Volume Anomaly",
            display_name="成交量异常均值回归",
            description="结合价格偏离移动平均线的程度与成交量异常放大（尤其是突破后缩量），预测短期反转。正值预示回调（做空），负值预示反弹（做多）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        ma_period = 20
        std_period = 10
        # 计算均线和标准差
        ma = data['close'].rolling(ma_period).mean()
        std = data['close'].rolling(std_period).std()
        # 标准化偏离
        z = (data['close'] - ma) / (std + 1e-10)
        # 成交量异常：当前成交量相对过去N日均值，并考虑变化方向
        vol_ma = data['volume'].rolling(ma_period).mean()
        vol_z = (data['volume'] - vol_ma) / (data['volume'].rolling(std_period).std() + 1e-10)
        # 反转信号：过度偏离且成交量异常（放量后可能衰竭）
        extreme = np.abs(z) > 2.0
        vol_extreme = np.abs(vol_z) > 1.5
        # 方向：正偏离预示回调（负值），负偏离预示反弹（正值）
        raw_signal = -np.sign(z) * np.minimum(np.abs(z) / 3.0, 1.0)
        # 仅当极端且成交量异常时增强
        boost = extreme & vol_extreme
        result = np.where(boost, raw_signal * 1.2, raw_signal * 0.5)
        result = np.clip(result, -1, 1)
        return pd.Series(result, index=data.index)
