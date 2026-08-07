"""AI因子: 止损集中度 | 置信:50% | 检测价格突破近期ATR倍数阈值时可能的止损单集中引爆区域。基于平均真实波幅(ATR)和价格偏离程度，当价格连续两次突破1.5倍ATR且回撤时，认为止损订单被触发并可能引发反向波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossConcentration(BaseFactor):
    """检测价格突破近期ATR倍数阈值时可能的止损单集中引爆区域。基于平均真实波幅(ATR)和价格偏离程度，当价格连续两次突破1.5倍ATR且回撤时，认为止损订单被触发并可能引发反向波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_slconc",
            name="Stop-Loss Concentration",
            display_name="止损集中度",
            description="检测价格突破近期ATR倍数阈值时可能的止损单集中引爆区域。基于平均真实波幅(ATR)和价格偏离程度，当价格连续两次突破1.5倍ATR且回撤时，认为止损订单被触发并可能引发反向波动。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # ATR
        tr = np.maximum(data['high'] - data['low'],
                        np.abs(data['high'] - data['close'].shift()),
                        np.abs(data['low'] - data['close'].shift()))
        atr = pd.Series(tr).rolling(14).mean()
        # 价格偏离参考移动平均
        ma = data['close'].rolling(20).mean()
        deviation = (data['close'] - ma) / atr
        # 检测偏离超过1.5后回撤
        over_extend = np.abs(deviation) > 1.5
        # 回撤信号：当前偏离绝对值小于前一期
        shrink = (np.abs(deviation) < np.abs(deviation.shift())) & over_extend.shift()
        signal = np.where(shrink & (deviation > 0), -1,  # 上方回撤 -> 看空
                          np.where(shrink & (deviation < 0), 1, 0))  # 下方回撤 -> 看多
        return pd.Series(signal, index=data.index)
