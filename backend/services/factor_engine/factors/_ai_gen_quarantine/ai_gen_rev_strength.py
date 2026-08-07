"""AI因子: 反转强度 | 置信:60% | 基于价格动量与超买超卖区域判断短期反转概率。计算过去N根K线的价格变化与当前收盘价相对近期高点的偏离，结合成交量确认。高正值表示强反转信号（做空风险大），低负值表示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalStrength(BaseFactor):
    """基于价格动量与超买超卖区域判断短期反转概率。计算过去N根K线的价格变化与当前收盘价相对近期高点的偏离，结合成交量确认。高正值表示强反转信号（做空风险大），低负值表示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_strength",
            name="Reversal Strength",
            display_name="反转强度",
            description="基于价格动量与超买超卖区域判断短期反转概率。计算过去N根K线的价格变化与当前收盘价相对近期高点的偏离，结合成交量确认。高正值表示强反转信号（做空风险大），低负值表示趋势延续。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数设置
        period = 14
        # 计算动量
        mom = data['close'].pct_change(period)
        # 计算RSI
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period, min_periods=1).mean()
        avg_loss = loss.rolling(period, min_periods=1).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 计算当前收盘价相对过去period天最高价的偏离
        highest = data['high'].rolling(period).max()
        deviation = (data['close'] - highest) / (highest + 1e-10)
        # 成交量确认：近期成交量放大
        vol_ma = data['volume'].rolling(period).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 综合信号：动量负且RSI低（超卖）且价格远离高点（短期超跌）时为正反转信号
        raw = ( (mom < -0.02).astype(int) * (rsi < 30).astype(int) * (deviation < -0.05).astype(int) * (vol_ratio > 1.2).astype(int) )
        # 平滑并映射到[-1,1]
        result = raw.rolling(3).mean().fillna(0) * 2 - 1
        return result.fillna(0).clip(-1, 1)
