"""AI因子: 流动性陷阱检测 | 置信:60% | 识别成交量异常放大后价格无法持续突破的现象。通过成交量与价格变动的协同性分析，输出负值表示可能即将发生反转（流动性陷阱），正值表示健康趋势延续。用于避免类似liq_magnet_reversal和ai_reverse的错误。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityTrapDetection(BaseFactor):
    """识别成交量异常放大后价格无法持续突破的现象。通过成交量与价格变动的协同性分析，输出负值表示可能即将发生反转（流动性陷阱），正值表示健康趋势延续。用于避免类似liq_magnet_reversal和ai_reverse的错误。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_trap",
            name="Liquidity Trap Detection",
            display_name="流动性陷阱检测",
            description="识别成交量异常放大后价格无法持续突破的现象。通过成交量与价格变动的协同性分析，输出负值表示可能即将发生反转（流动性陷阱），正值表示健康趋势延续。用于避免类似liq_magnet_reversal和ai_reverse的错误。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算成交量相对其20日均值的异常
        vol_ma = volume.rolling(window=20).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 价格变化率（1期）
        ret = close.pct_change()
        # 计算成交量与价格变动的协动性：高成交量伴随高价格变化正相关？
        # 用滚动相关系数（窗口5）
        corr = ret.rolling(window=5).corr(vol_ratio.rolling(window=5).mean().shift(1))
        # 成交量冲击指数：成交量比高但价格变化小或反向
        vol_shock = vol_ratio * np.sign(ret)  # 正表示放量上涨，负放量下跌
        # 结合相关系数：当相关系数突然下跌时，可能是陷阱
        corr_change = corr - corr.shift(5)
        # 构造信号
        trap_signal = -np.sign(vol_shock) * np.abs(corr_change) * np.clip(vol_ratio-1, 0, 10)
        trap_signal = np.clip(trap_signal, -1, 1)
        # 平滑
        result = trap_signal.rolling(window=3).mean().fillna(0)
        return result
