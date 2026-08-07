"""AI因子: 止损集中反转指标 | 置信:55% | 通过价格突破近期支撑/阻力位时的成交量变化和后续价格行为，判断是否存在止损单触发后的假反转。计算价格突破移动平均线（如20日均线）且成交量放大，但随后K线实体未能站稳该线，表明止损单被清理后价格反向。因子值为正时表示当前出现止损集中信号，应避免反向开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossClusterReversalIndicator(BaseFactor):
    """通过价格突破近期支撑/阻力位时的成交量变化和后续价格行为，判断是否存在止损单触发后的假反转。计算价格突破移动平均线（如20日均线）且成交量放大，但随后K线实体未能站稳该线，表明止损单被清理后价格反向。因子值为正时表示当前出现止损集中信号，应避免反向开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_hunt",
            name="Stop Loss Cluster Reversal Indicator",
            display_name="止损集中反转指标",
            description="通过价格突破近期支撑/阻力位时的成交量变化和后续价格行为，判断是否存在止损单触发后的假反转。计算价格突破移动平均线（如20日均线）且成交量放大，但随后K线实体未能站稳该线，表明止损单被清理后价格反向。因子值为正时表示当前出现止损集中信号，应避免反向开仓。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        ma_period = 20
        lookback = 5
        # 移动平均线
        ma = data['close'].rolling(ma_period).mean()
        # 价格偏离MA
        deviation = (data['close'] - ma) / (ma + 1e-10)
        # 成交量变化率
        vol_ratio = data['volume'] / data['volume'].rolling(ma_period).mean()
        # 突破条件：价格从下方穿过MA（上穿）或从上方穿过（下穿）
        cross_up = (data['close'] > ma) & (data['close'].shift(1) <= ma.shift(1))
        cross_down = (data['close'] < ma) & (data['close'].shift(1) >= ma.shift(1))
        # 假突破信号：突破后成交量放大但随后一根K线收盘返回MA另一侧
        fake_up = cross_up & (vol_ratio > 1.5) & (data['close'].shift(-1) < ma.shift(-1))
        fake_down = cross_down & (vol_ratio > 1.5) & (data['close'].shift(-1) > ma.shift(-1))
        # 合并信号，用shift对齐
        signal = fake_up.astype(int) - fake_down.astype(int)
        signal = signal.shift(1).fillna(0)
        # 强度用当前偏离的绝对值乘以信号
        raw = signal * np.abs(deviation) * 2
        raw = np.clip(raw, -1, 1)
        return pd.Series(raw, index=data.index)
