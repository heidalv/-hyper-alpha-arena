"""AI因子: 流动性陷阱反转 | 置信:60% | 检测价格剧烈变动后出现窄幅K线且成交量放大，预示流动性陷阱可能引发价格反转。返回正值为反转信号，负值为无信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityTrap(BaseFactor):
    """检测价格剧烈变动后出现窄幅K线且成交量放大，预示流动性陷阱可能引发价格反转。返回正值为反转信号，负值为无信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_trap",
            name="Liquidity_Trap",
            display_name="流动性陷阱反转",
            description="检测价格剧烈变动后出现窄幅K线且成交量放大，预示流动性陷阱可能引发价格反转。返回正值为反转信号，负值为无信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 计算价格变动幅度（过去5根K线）
        range5 = high.rolling(5).max() - low.rolling(5).min()
        price_range = range5 / close.shift(5) * 100  # 百分比
        # 当前K线实体内占比
        body = np.abs(close - data['open'])
        body_ratio = body / (high - low + 1e-10)
        # 成交量放大倍数（相对MA20）
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 条件：过去5日波动大（>2%）且当前实体小（<0.4）且成交量放大（>1.5倍）
        cond = (price_range > 2) & (body_ratio < 0.4) & (vol_ratio > 1.5)
        # 方向：若前5日上涨则预测下跌，反之预测上涨
        trend = (close - close.shift(5)) / close.shift(5)
        direction = np.where(cond, np.where(trend > 0, -1, 1), 0)
        result = pd.Series(direction, index=close.index).fillna(0)
        return result
