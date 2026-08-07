"""AI因子: 成交量不稳定度 | 置信:65% | 度量成交量相对于其近期均值的偏离程度，偏离越大表示流动性或参与度不稳定，容易导致滑点或异常止损，赋值负值。使用当前成交量与过去N天均值比值的对数，经标准化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Instability(BaseFactor):
    """度量成交量相对于其近期均值的偏离程度，偏离越大表示流动性或参与度不稳定，容易导致滑点或异常止损，赋值负值。使用当前成交量与过去N天均值比值的对数，经标准化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_instability",
            name="Volume Instability",
            display_name="成交量不稳定度",
            description="度量成交量相对于其近期均值的偏离程度，偏离越大表示流动性或参与度不稳定，容易导致滑点或异常止损，赋值负值。使用当前成交量与过去N天均值比值的对数，经标准化至[-1,1]。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        volume = data['volume']
        mean_vol = volume.rolling(n, min_periods=5).mean()
        # 避免除零
        ratio = volume / (mean_vol + 1e-10)
        # 对数偏离，取绝对值后归一化：偏离越大越负
        log_ratio = np.log(ratio + 1e-10)
        # 用指数移动平均平滑并映射，偏离>1.5倍标准差则负
        std_ratio = log_ratio.rolling(n).std()
        z = (log_ratio - log_ratio.rolling(n).mean()) / (std_ratio + 1e-10)
        # 将z分数压缩到[-1,1]，负z表示异常低或高? 这里我们只关心不稳定：无论过高或过低都危险，所以取绝对值
        # 使用sigmoid变形：1-2/(1+exp(-|z|)) 得到[-1,0]区间
        abs_z = np.abs(z)
        result = 1 - 2 / (1 + np.exp(-abs_z))
        return result.fillna(0).clip(-1, 1)
