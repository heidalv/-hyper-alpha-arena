"""AI因子: 尘埃清理波动收缩 | 置信:60% | 捕捉短期剧烈波动后波动率快速收缩的形态，类似'清理浮筹'后的方向选择。当价格在高波动后进入窄幅整理且成交量萎缩，空头容易在后续被反向突破。因子值为-1表示空头风险。计算逻辑：计算过去N周期真实波幅均值，若近期波幅收缩至某阈值以下，且价格处于近期区间中下部，同时成交量低于均值，则输出-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupVolatilityContraction(BaseFactor):
    """捕捉短期剧烈波动后波动率快速收缩的形态，类似'清理浮筹'后的方向选择。当价格在高波动后进入窄幅整理且成交量萎缩，空头容易在后续被反向突破。因子值为-1表示空头风险。计算逻辑：计算过去N周期真实波幅均值，若近期波幅收缩至某阈值以下，且价格处于近期区间中下部，同时成交量低于均值，则输出-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_cleanup",
            name="Dust Cleanup Volatility Contraction",
            display_name="尘埃清理波动收缩",
            description="捕捉短期剧烈波动后波动率快速收缩的形态，类似'清理浮筹'后的方向选择。当价格在高波动后进入窄幅整理且成交量萎缩，空头容易在后续被反向突破。因子值为-1表示空头风险。计算逻辑：计算过去N周期真实波幅均值，若近期波幅收缩至某阈值以下，且价格处于近期区间中下部，同时成交量低于均值，则输出-1。",
            category="volatility",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        N = 10
        K = 20
    
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
    
        # 真实波幅
        prev_close = close.shift(1)
        tr = np.maximum(high - low, 
                       np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        atr = tr.rolling(N).mean()
    
        # 波幅收缩：最近N期ATR相对于过去K期最大值比例
        max_atr = atr.rolling(K).max()
        atr_ratio = atr / max_atr.replace(0, np.nan)
    
        # 价格位置：当前收盘在近N期高低中的位置
        rolling_high = high.rolling(N).max()
        rolling_low = low.rolling(N).min()
        price_pos = (close - rolling_low) / (rolling_high - rolling_low).replace(0, np.nan)
    
        # 成交量萎缩：当前成交量低于近M期均值
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma.replace(0, np.nan)
    
        # 条件：波幅小于最大值的50%，价格位置低于0.4，成交量小于均值
        condition = (atr_ratio < 0.5) & (price_pos < 0.4) & (vol_ratio < 0.8)
    
        # 输出：条件成立时-1，否则0
        result = pd.Series(np.where(condition, -1.0, 0.0), index=data.index)
        return result
