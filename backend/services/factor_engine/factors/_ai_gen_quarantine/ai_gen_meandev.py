"""AI因子: 均值回归偏离度 | 置信:60% | 衡量当前收盘价相对于布林带中轨（20日均线）的标准化偏离。正值表示价格高于中轨（超买），负值表示低于中轨（超卖）。在无趋势震荡市中，极端偏离预示回归，适合逆向操作。值域[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionDeviation(BaseFactor):
    """衡量当前收盘价相对于布林带中轨（20日均线）的标准化偏离。正值表示价格高于中轨（超买），负值表示低于中轨（超卖）。在无趋势震荡市中，极端偏离预示回归，适合逆向操作。值域[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_meandev",
            name="Mean Reversion Deviation",
            display_name="均值回归偏离度",
            description="衡量当前收盘价相对于布林带中轨（20日均线）的标准化偏离。正值表示价格高于中轨（超买），负值表示低于中轨（超卖）。在无趋势震荡市中，极端偏离预示回归，适合逆向操作。值域[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 20
        ma = data['close'].rolling(window=period).mean()
        std = data['close'].rolling(window=period).std()
        # Z-score，截断后除以3归一化
        z = (data['close'] - ma) / (std + 1e-10)
        # 压缩到[-1,1]
        result = pd.Series(np.clip(z / 3, -1, 1), index=data.index)
        result = result.fillna(0)
        return result
