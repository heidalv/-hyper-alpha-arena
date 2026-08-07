"""AI因子: 成交量确认因子 | 置信:70% | 衡量成交量与价格变动的同步性。当价格上涨伴随成交量放大时，趋势可靠，信号为正；缩量上涨或放量下跌时，趋势不可信，信号为负。有助于避免在无量反弹中做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConviction(BaseFactor):
    """衡量成交量与价格变动的同步性。当价格上涨伴随成交量放大时，趋势可靠，信号为正；缩量上涨或放量下跌时，趋势不可信，信号为负。有助于避免在无量反弹中做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumeconviction",
            name="VolumeConviction",
            display_name="成交量确认因子",
            description="衡量成交量与价格变动的同步性。当价格上涨伴随成交量放大时，趋势可靠，信号为正；缩量上涨或放量下跌时，趋势不可信，信号为负。有助于避免在无量反弹中做多。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化
        price_change = close.pct_change()
        # 成交量变化率
        vol_change = volume.pct_change()
        # 计算滚动相关系数（14日窗口）
        corr = price_change.rolling(14).corr(vol_change)
        # 同时考虑价格动量方向：若价格上升且相关系数为正则强
        # 使用符号：corr * 价格变化方向
        direction = np.sign(price_change)
        # 信号 = corr * direction，但需要处理NaN
        signal = corr * direction
        # 限制在[-1,1]
        signal = np.clip(signal, -1, 1)
        return pd.Series(signal, index=close.index).fillna(0)
