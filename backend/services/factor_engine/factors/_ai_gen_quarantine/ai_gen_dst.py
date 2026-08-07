"""AI因子: 灰尘清理规避因子 | 置信:50% | 捕捉因小订单或大单平仓引起的短暂异常脉冲后回归的形态。通过检测价格在短时间内出现异常尖峰（相对于上下影线）且成交量突增，随后回补缺口。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupAvoidance(BaseFactor):
    """捕捉因小订单或大单平仓引起的短暂异常脉冲后回归的形态。通过检测价格在短时间内出现异常尖峰（相对于上下影线）且成交量突增，随后回补缺口。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dst",
            name="Dust Cleanup Avoidance",
            display_name="灰尘清理规避因子",
            description="捕捉因小订单或大单平仓引起的短暂异常脉冲后回归的形态。通过检测价格在短时间内出现异常尖峰（相对于上下影线）且成交量突增，随后回补缺口。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算上下影线长度
        df['upper_wick'] = df['high'] - df[['open','close']].max(axis=1)
        df['lower_wick'] = df[['open','close']].min(axis=1) - df['low']
        body = abs(df['close'] - df['open'])
        # 影线占比过大且成交量放大
        wick_ratio = (df['upper_wick'] + df['lower_wick']) / (body + 1e-8)
        vol_ma = df['volume'].rolling(20).mean()
        vol_ratio = df['volume'] / (vol_ma + 1e-8)
        # 条件：影线长度大于实体2倍且成交量大于均值2倍
        signal = pd.Series(0.0, index=df.index)
        cond = (wick_ratio > 2.0) & (vol_ratio > 2.0)
        # 根据收盘相对开盘的方向判断回归方向：如果收盘高于开盘（阳线）但上影线过长，预示下跌回归；反之预示上涨回归
        increase = df['close'] > df['open']
        signal.loc[cond & increase] = -0.8  # 看空
        signal.loc[cond & ~increase] = 0.8   # 看多
        return signal
