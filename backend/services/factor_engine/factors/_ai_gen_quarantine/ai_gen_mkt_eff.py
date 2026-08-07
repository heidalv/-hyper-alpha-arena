"""AI因子: 市场效率系数 | 置信:60% | 基于最近20周期收盘价路径的长度与起点到终点直线距离的比值，衡量市场是否具有趋势性。比值接近1表示强趋势，比值远大于1表示随机震荡。将比值取倒数并压缩到[-1,1]，使得有效趋势对应正值，无效震荡对应负值，从而识别未知状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketEfficiency(BaseFactor):
    """基于最近20周期收盘价路径的长度与起点到终点直线距离的比值，衡量市场是否具有趋势性。比值接近1表示强趋势，比值远大于1表示随机震荡。将比值取倒数并压缩到[-1,1]，使得有效趋势对应正值，无效震荡对应负值，从而识别未知状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mkt_eff",
            name="MarketEfficiency",
            display_name="市场效率系数",
            description="基于最近20周期收盘价路径的长度与起点到终点直线距离的比值，衡量市场是否具有趋势性。比值接近1表示强趋势，比值远大于1表示随机震荡。将比值取倒数并压缩到[-1,1]，使得有效趋势对应正值，无效震荡对应负值，从而识别未知状态。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        window = 10
        # 计算每一段的路径总长度
        path = close.diff().abs().rolling(window).sum()
        # 起点到终点距离
        displacement = (close - close.shift(window)).abs()
        # 避免除以零
        displacement = displacement.replace(0, 1e-10)
        efficiency = displacement / (path + 1e-10)
        # 映射到[-1,1]: 效率低=>负, 效率高=>正
        result = (efficiency - 0.5) * 2
        result = result.clip(-1, 1)
        return result
