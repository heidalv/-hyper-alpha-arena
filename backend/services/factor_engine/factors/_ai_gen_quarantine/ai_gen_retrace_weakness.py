"""AI因子: 回撤弱势因子 | 置信:50% | 衡量价格回撤至均线附近时的动能强度。当价格从高点回落至20日均线附近且成交量未能放大，反弹乏力，预示继续下跌或震荡，因子为负值；反之有效支撑后放量上涨则为正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class retracement_weakness(BaseFactor):
    """衡量价格回撤至均线附近时的动能强度。当价格从高点回落至20日均线附近且成交量未能放大，反弹乏力，预示继续下跌或震荡，因子为负值；反之有效支撑后放量上涨则为正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_retrace_weakness",
            name="retracement_weakness",
            display_name="回撤弱势因子",
            description="衡量价格回撤至均线附近时的动能强度。当价格从高点回落至20日均线附近且成交量未能放大，反弹乏力，预示继续下跌或震荡，因子为负值；反之有效支撑后放量上涨则为正值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 20日均线
        ma20 = close.rolling(20).mean()
        # 计算近期高点（10日最高）
        recent_high = high.rolling(10).max()
        # 价格距均线距离
        dist_to_ma = (close - ma20) / ma20
        # 判断是否处于回撤状态：当前价低于近期高点且接近均线
        is_retrace = (close < recent_high) & (abs(dist_to_ma) < 0.03)
        # 成交量变化：当前量相对于过去5日均量
        vol_ma5 = volume.rolling(5).mean()
        vol_ratio = volume / vol_ma5
        # 动量：短期价格变化
        mom = close.pct_change(3)
        # 信号合成
        factor = np.where(is_retrace,
                          np.clip((vol_ratio - 1) * 2 + mom * 5, -1, 1),
                          np.clip(mom * 2, -1, 1))
        factor = pd.Series(factor, index=close.index).fillna(0)
        return factor
