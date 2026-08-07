"""AI因子: 尘埃清理检测 | 置信:55% | 识别微小成交量推动价格小幅波动后反向的'尘埃清理'模式。通过计算价格变化与成交量的背离程度，当价格微涨但成交量极低或价格微跌但成交量放大时，预示后续反向运动。旨在规避'dust_cleanup'亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupDetector(BaseFactor):
    """识别微小成交量推动价格小幅波动后反向的'尘埃清理'模式。通过计算价格变化与成交量的背离程度，当价格微涨但成交量极低或价格微跌但成交量放大时，预示后续反向运动。旨在规避'dust_cleanup'亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust",
            name="Dust Cleanup Detector",
            display_name="尘埃清理检测",
            description="识别微小成交量推动价格小幅波动后反向的'尘埃清理'模式。通过计算价格变化与成交量的背离程度，当价格微涨但成交量极低或价格微跌但成交量放大时，预示后续反向运动。旨在规避'dust_cleanup'亏损。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化百分比
        pct_chg = close.pct_change()
        # 成交量相对变化（对数成交量的差分近似）
        vol_chg = np.log(volume + 1).diff()
        # 背离指标：价格小涨但成交量萎缩，或价格小跌但成交量放大
        # 计算过去5根K线内的小波动（价格变化绝对值<0.5%）
        small_move = pct_chg.abs() < 0.005
        # 成交量变化方向（负为萎缩，正为放大）
        # 条件: 价格微涨(pct_chg>0)且成交量萎缩(vol_chg< -0.1) => 看空
        # 条件: 价格微跌(pct_chg<0)且成交量放大(vol_chg> 0.1) => 看多? 但此类模式常反向，此处统一做反转
        signal = np.where(
            (small_move) & (pct_chg > 0) & (vol_chg < -0.1), -1,
            np.where(
                (small_move) & (pct_chg < 0) & (vol_chg > 0.1), 1,
                0
            )
        )
        result = pd.Series(signal, index=data.index)
        # 平滑处理，取过去3根信号的最大值作为最终信号
        result = result.rolling(3, min_periods=1).max()
        return result.clip(-1, 1)
