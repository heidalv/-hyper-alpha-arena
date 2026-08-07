"""AI因子: 假突破识别因子 | 置信:65% | 基于20日布林带（2倍标准差）判断价格突破上下轨后是否快速返回带内，若收盘价突破上轨但下一根K线回到带内，则视为假突破，生成负信号。反之亦然。适用于规避regime=unknown下的假趋势信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakoutDetector(BaseFactor):
    """基于20日布林带（2倍标准差）判断价格突破上下轨后是否快速返回带内，若收盘价突破上轨但下一根K线回到带内，则视为假突破，生成负信号。反之亦然。适用于规避regime=unknown下的假趋势信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fake_break",
            name="Fake Breakout Detector",
            display_name="假突破识别因子",
            description="基于20日布林带（2倍标准差）判断价格突破上下轨后是否快速返回带内，若收盘价突破上轨但下一根K线回到带内，则视为假突破，生成负信号。反之亦然。适用于规避regime=unknown下的假趋势信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        df['ma'] = df['close'].rolling(20).mean()
        df['std'] = df['close'].rolling(20).std()
        df['upper'] = df['ma'] + 2 * df['std']
        df['lower'] = df['ma'] - 2 * df['std']
        # 当前是否突破上轨
        breakout_up = df['close'] > df['upper']
        # 上一根K线突破，当前回落至带内
        fake_up = breakout_up.shift(1) & (df['close'] <= df['upper'])
        breakout_down = df['close'] < df['lower']
        fake_down = breakout_down.shift(1) & (df['close'] >= df['lower'])
        # 信号：假多头突破给-1（看空），假空头突破给+1（看多），其他0
        signal = pd.Series(0, index=df.index)
        signal[fake_up] = -1.0
        signal[fake_down] = 1.0
        return signal
