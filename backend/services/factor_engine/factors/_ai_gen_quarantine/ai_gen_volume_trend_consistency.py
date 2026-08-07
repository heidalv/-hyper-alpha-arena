"""AI因子: 量价趋势一致性 | 置信:55% | 通过计算价格变化与成交量变化的滚动相关系数，衡量量价关系是否一致。正相关系数表示趋势健康（价升量增或价跌量缩），因子为正；负相关表示背离（价升量缩或价跌量增），因子为负。可用于识别master_running_close等异常平仓场景。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeTrendConsistency(BaseFactor):
    """通过计算价格变化与成交量变化的滚动相关系数，衡量量价关系是否一致。正相关系数表示趋势健康（价升量增或价跌量缩），因子为正；负相关表示背离（价升量缩或价跌量增），因子为负。可用于识别master_running_close等异常平仓场景。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_trend_consistency",
            name="Volume-Trend Consistency",
            display_name="量价趋势一致性",
            description="通过计算价格变化与成交量变化的滚动相关系数，衡量量价关系是否一致。正相关系数表示趋势健康（价升量增或价跌量缩），因子为正；负相关表示背离（价升量缩或价跌量增），因子为负。可用于识别master_running_close等异常平仓场景。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算收益率和成交量变化率
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 滚动窗口计算相关系数
        window = 20
        corr = ret.rolling(window).corr(vol_change)
        # 处理缺失值
        corr = corr.fillna(0)
        # 直接使用相关系数作为因子，范围[-1,1]
        return corr
