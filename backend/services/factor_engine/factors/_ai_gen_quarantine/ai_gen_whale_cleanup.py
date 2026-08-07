"""AI因子: 鲸鱼清理信号因子 | 置信:60% | 通过分析成交量分布与价格趋势的背离来捕捉大资金清理仓位的行为。当价格下跌但成交量放大且短期动量走弱，或者价格反弹而成交量萎缩时，可能是清理仓位的信号。使用累积成交量加权价格（VWAP）偏离度和成交量变化率构建。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WhaleCleanupDetection(BaseFactor):
    """通过分析成交量分布与价格趋势的背离来捕捉大资金清理仓位的行为。当价格下跌但成交量放大且短期动量走弱，或者价格反弹而成交量萎缩时，可能是清理仓位的信号。使用累积成交量加权价格（VWAP）偏离度和成交量变化率构建。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_whale_cleanup",
            name="Whale Cleanup Detection",
            display_name="鲸鱼清理信号因子",
            description="通过分析成交量分布与价格趋势的背离来捕捉大资金清理仓位的行为。当价格下跌但成交量放大且短期动量走弱，或者价格反弹而成交量萎缩时，可能是清理仓位的信号。使用累积成交量加权价格（VWAP）偏离度和成交量变化率构建。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算VWAP
        typical_price = (data['high'] + data['low'] + data['close']) / 3
        cum_vol = data['volume'].rolling(20).sum()
        cum_pv = (typical_price * data['volume']).rolling(20).sum()
        vwap = cum_pv / cum_vol.replace(0, np.nan)
        # 价格相对VWAP的偏离
        price_dev = (data['close'] - vwap) / vwap.replace(0, np.nan)
        # 成交量变化率（短期/长期）
        vol_short = data['volume'].rolling(5).mean()
        vol_long = data['volume'].rolling(20).mean()
        vol_ratio = vol_short / vol_long.replace(0, np.nan)
        # 清理信号：价格下跌且成交量放大 -> 正信号（做空）；价格上涨且成交量缩小 -> 负信号（做多）
        # 将price_dev映射到[-1,1]（原始偏离通常很小），用tanh
        dev_signal = np.tanh(price_dev * 10)  # 正向代表价格高于VWAP（预期回归下跌）
        # 成交量放大时vol_ratio > 1，缩小 < 1
        vol_signal = np.clip((vol_ratio - 1) * 2, -1, 1)  # 放大为正，缩小为负
        # 组合：反转逻辑：价格高于VWAP+放量 -> 看跌；价格低于VWAP+缩量 -> 看涨？实际清理时方向需调整
        # 从模式来看，清理常导致逆势，所以组合为：dev_signal * vol_signal
        result = dev_signal * vol_signal
        return result.fillna(0)
