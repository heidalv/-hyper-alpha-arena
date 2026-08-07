"""AI因子: 量价背离指数 | 置信:60% | 检测价格变动与成交量确认的背离程度。当价格突破但成交量没有放大时，假突破概率高（因子趋近-1）；当价格变动有成交量确认时，趋势健康（趋近+1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """检测价格变动与成交量确认的背离程度。当价格突破但成交量没有放大时，假突破概率高（因子趋近-1）；当价格变动有成交量确认时，趋势健康（趋近+1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumeprice",
            name="Volume-Price Divergence",
            display_name="量价背离指数",
            description="检测价格变动与成交量确认的背离程度。当价格突破但成交量没有放大时，假突破概率高（因子趋近-1）；当价格变动有成交量确认时，趋势健康（趋近+1）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格变化百分比
        ret = data['close'].pct_change()
        # 计算成交量变化百分比
        vol = data['volume']
        vol_change = vol.pct_change()
        # 计算价格变化的方向：1为正，-1为负
        price_direction = np.sign(ret)
        # 成交量变化的方向：1为增，-1为减
        vol_direction = np.sign(vol_change)
        # 如果价格和成交量同向（上涨时放量，下跌时缩量），则因子为正，否则为负
        # 同时考虑幅度：用成交量变化幅度加权
        vol_magnitude = np.abs(vol_change).clip(0, 1)  # 限制最大为1
        # 协同度：同向为1，反向为-1
        coher = price_direction * vol_direction
        # 加权：协同度乘以成交量幅度，再平滑
        raw = coher * vol_magnitude
        # 用3周期移动平均减少噪音
        result = pd.Series(raw).rolling(3).mean()
        # 填充NaN并限制范围
        result = result.clip(-1, 1).fillna(0)
        return pd.Series(result, index=data.index)
