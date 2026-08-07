"""AI因子: 流动性扫荡反转 | 置信:65% | 捕捉价格快速突破后伴随成交量异常放大并迅速回撤的流动性陷阱行为。当价格短时间内偏离均线且成交量飙升超过阈值，随后回调，产生做空信号。基于错误模式中的liq_magnet_reversal和dust_cleanup。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquiditySweepReversal(BaseFactor):
    """捕捉价格快速突破后伴随成交量异常放大并迅速回撤的流动性陷阱行为。当价格短时间内偏离均线且成交量飙升超过阈值，随后回调，产生做空信号。基于错误模式中的liq_magnet_reversal和dust_cleanup。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_sweep",
            name="Liquidity Sweep Reversal",
            display_name="流动性扫荡反转",
            description="捕捉价格快速突破后伴随成交量异常放大并迅速回撤的流动性陷阱行为。当价格短时间内偏离均线且成交量飙升超过阈值，随后回调，产生做空信号。基于错误模式中的liq_magnet_reversal和dust_cleanup。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算短期均线（5周期）和长期均线（20周期）
        df['sma5'] = df['close'].rolling(5).mean()
        df['sma20'] = df['close'].rolling(20).mean()
        # 计算价格偏离度
        df['dev'] = (df['close'] - df['sma20']) / df['sma20']
        # 计算成交量异常：当前成交量与过去20日平均成交量的比值
        df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
        # 计算短期波动（5日ATR归一化）
        atr = (df['high'] - df['low']).rolling(5).mean()
        df['atr_norm'] = atr / df['close']
        # 构造信号：价格突破 + 成交量异常 + 随后回撤
        # 首先，识别价格向上突破（dev > 0.02 且 vol_ratio > 2）
        cond_bull = (df['dev'] > 0.02) & (df['vol_ratio'] > 2)
        # 然后检测后续是否回撤：下一根K线close低于当前close + 小窗口内最低价
        cond_reversal = cond_bull.shift(1) & (df['close'] < df['close'].shift(1) - df['atr_norm'].shift(1)*df['close'].shift(1)*1.0)
        # 向下突破同理（做多信号）
        cond_bear = (df['dev'] < -0.02) & (df['vol_ratio'] > 2)
        cond_rev_up = cond_bear.shift(1) & (df['close'] > df['close'].shift(1) + df['atr_norm'].shift(1)*df['close'].shift(1)*1.0)
        # 综合信号：做空信号为-1，做多信号为+1
        signal = pd.Series(0, index=df.index)
        signal[cond_reversal] = -1.0
        signal[cond_rev_up] = 1.0
        return signal
