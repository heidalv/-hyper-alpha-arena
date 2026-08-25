"""AI因子: 量价流 | 置信:50% | 结合价格变化与成交量异常，衡量资金流入流出强度。计算当日成交量相较于过去20日均值的偏离，并与价格方向相乘，再经标准化映射到[-1,1]。在量价背离时给出负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceFlow(BaseFactor):
    """结合价格变化与成交量异常，衡量资金流入流出强度。计算当日成交量相较于过去20日均值的偏离，并与价格方向相乘，再经标准化映射到[-1,1]。在量价背离时给出负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_flow",
            name="Volume Price Flow",
            display_name="量价流",
            description="结合价格变化与成交量异常，衡量资金流入流出强度。计算当日成交量相较于过去20日均值的偏离，并与价格方向相乘，再经标准化映射到[-1,1]。在量价背离时给出负信号。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        # 成交量比率
        vol_ma = df['volume'].rolling(20).mean()
        vol_ratio = df['volume'] / (vol_ma + 1e-8) - 1.0
        # 价格变化方向（当日收盘-前收盘）
        price_change = df['close'].diff() / df['close'].shift(1)
        # 量价方向一致则正向，背离则负向
        flow = vol_ratio * np.sign(price_change)
        # 平滑和标准化
        flow_ma = flow.rolling(20).mean()
        flow_std = flow.rolling(20).std()
        zscore = (flow - flow_ma) / (flow_std + 1e-8)
        clipped = np.clip(zscore, -3, 3) / 3.0
        result = pd.Series(clipped, index=df.index).fillna(0.0)
        return result
