"""AI因子: 微幅反转信号 | 置信:55% | 检测价格在窄幅震荡中成交量异常放大但价格未突破，类似reverse_netting亏损场景。通过比较价格变化量与成交量比值，并识别成交量的极端放大但价格变动微弱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicroReversalSignal(BaseFactor):
    """检测价格在窄幅震荡中成交量异常放大但价格未突破，类似reverse_netting亏损场景。通过比较价格变化量与成交量比值，并识别成交量的极端放大但价格变动微弱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrs",
            name="Micro Reversal Signal",
            display_name="微幅反转信号",
            description="检测价格在窄幅震荡中成交量异常放大但价格未突破，类似reverse_netting亏损场景。通过比较价格变化量与成交量比值，并识别成交量的极端放大但价格变动微弱。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
    
        # 价格变化率绝对值
        price_change = close.pct_change().abs()
        # 成交量变化率
        vol_change = volume.pct_change().abs()
        # 日内振幅
        amplitude = (high - low) / close
    
        # 微幅反转条件：价格变化小（<0.5%）但成交量急剧放大（>2倍）且振幅小
        condition1 = (price_change < 0.005) & (vol_change > 1.0) & (amplitude < 0.02)
        # 同时要求过去5日平均成交量不高（避免流动性大的正常波动）
        avg_vol = volume.rolling(5).mean()
        condition2 = volume > avg_vol * 1.5
        # 组合
        signal = condition1 & condition2
        # 转换为连续信号：用成交量放大倍数加权
        magnitude = np.where(signal, (vol_change - 1.0).clip(0, 2) / 2.0, 0)
        # 将信号映射到[-1,1]，正信号表示即将反转的风险
        result = pd.Series(magnitude * 2 - 1, index=close.index)
        result = result.clip(-1, 1)
        return result
