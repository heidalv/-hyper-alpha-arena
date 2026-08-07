"""AI因子: 成交量放量确认 | 置信:60% | 基于当前成交量与20周期平均成交量的比率，结合价格方向（上涨/下跌）生成信号。成交量放大且价格上涨时为正（多头确认），成交量放大且价格下跌时为负（空头确认），成交量平稳时接近0。使用log抑制极端比值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeSurgeConfirmation(BaseFactor):
    """基于当前成交量与20周期平均成交量的比率，结合价格方向（上涨/下跌）生成信号。成交量放大且价格上涨时为正（多头确认），成交量放大且价格下跌时为负（空头确认），成交量平稳时接近0。使用log抑制极端比值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_surge",
            name="Volume Surge Confirmation",
            display_name="成交量放量确认",
            description="基于当前成交量与20周期平均成交量的比率，结合价格方向（上涨/下跌）生成信号。成交量放大且价格上涨时为正（多头确认），成交量放大且价格下跌时为负（空头确认），成交量平稳时接近0。使用log抑制极端比值。",
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
        # 价格变动方向（当前bar收盘相对于前bar收盘）
        price_change = close.diff()
        # 成交量比率
        vol_ma = volume.rolling(20, min_periods=20).mean()
        vol_ratio = volume / vol_ma
        # 使用log变换压制极端值，并对符号处理
        log_ratio = np.log(vol_ratio.clip(lower=1e-6, upper=None))  # 避免0
        # 方向：上涨为正，下跌为负
        direction = np.sign(price_change)
        # 结合方向，并压缩到[-1,1]（tanh）
        raw = direction * log_ratio
        result = np.tanh(raw.clip(-10, 10))  # 限制防止nan
        return result
