"""AI因子: 日内路径不规则度 | 置信:55% | 利用日内的开盘价、最高价、最低价、收盘价之间的相对位置来判断价格行为是否异常。计算 (close - open) 与 (high - low) 的比例，以及开盘后价格是否经常反向。具体逻辑：当close - open与high - low的比值（即收开盘差占全幅比例）接近0时，表示价格来回震荡，属于不规则路径，可能为未知状态。将比值绝对值化后映射到[-1,1]，越接近0（不规则），因子值越接近-1（空头信号？实际可统一为负值表示危险）。简化：因子 = 1 - 2 * abs((close - open) / (high - low + 1e-10))，使得当收盘靠近开盘时，因子接近-1（不规则路径），收盘靠近极值时，因子接近+1（趋势明确）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Intraday_Path_Irregularity(BaseFactor):
    """利用日内的开盘价、最高价、最低价、收盘价之间的相对位置来判断价格行为是否异常。计算 (close - open) 与 (high - low) 的比例，以及开盘后价格是否经常反向。具体逻辑：当close - open与high - low的比值（即收开盘差占全幅比例）接近0时，表示价格来回震荡，属于不规则路径，可能为未知状态。将比值绝对值化后映射到[-1,1]，越接近0（不规则），因子值越接近-1（空头信号？实际可统一为负值表示危险）。简化：因子 = 1 - 2 * abs((close - open) / (high - low + 1e-10))，使得当收盘靠近开盘时，因子接近-1（不规则路径），收盘靠近极值时，因子接近+1（趋势明确）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pricepath",
            name="Intraday Path Irregularity",
            display_name="日内路径不规则度",
            description="利用日内的开盘价、最高价、最低价、收盘价之间的相对位置来判断价格行为是否异常。计算 (close - open) 与 (high - low) 的比例，以及开盘后价格是否经常反向。具体逻辑：当close - open与high - low的比值（即收开盘差占全幅比例）接近0时，表示价格来回震荡，属于不规则路径，可能为未知状态。将比值绝对值化后映射到[-1,1]，越接近0（不规则），因子值越接近-1（空头信号？实际可统一为负值表示危险）。简化：因子 = 1 - 2 * abs((close - open) / (high - low + 1e-10))，使得当收盘靠近开盘时，因子接近-1（不规则路径），收盘靠近极值时，因子接近+1（趋势明确）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 防止分母为零
        span = data['high'] - data['low'] + 1e-10
        # 收盘相对开盘的位置比例
        pos_ratio = (data['close'] - data['open']) / span
        # 不规则度：值越接近0表示收盘在中间，路径不规则
        irregularity = 1 - 2 * abs(pos_ratio)
        return irregularity.clip(-1, 1).fillna(0)
