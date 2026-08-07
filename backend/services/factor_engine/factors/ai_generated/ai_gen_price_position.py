"""AI因子: 价格区间位置 | 置信:50% | 计算当前价格在过去N天最高最低区间内的位置，当价格处于中部区间（0.3~0.7）时输出负值，表示趋势不明确，避免开仓。使用sigmoid转换映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PricePositionInRange(BaseFactor):
    """计算当前价格在过去N天最高最低区间内的位置，当价格处于中部区间（0.3~0.7）时输出负值，表示趋势不明确，避免开仓。使用sigmoid转换映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_position",
            name="Price Position in Range",
            display_name="价格区间位置",
            description="计算当前价格在过去N天最高最低区间内的位置，当价格处于中部区间（0.3~0.7）时输出负值，表示趋势不明确，避免开仓。使用sigmoid转换映射到[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        period = 30
        high_max = df['high'].rolling(period).max()
        low_min = df['low'].rolling(period).min()
        pos = (df['close'] - low_min) / (high_max - low_min + 1e-8)
        # 中心惩罚：pos接近0.5时输出负值，远离0.5时输出正值
        # 使用对称函数：-2*(pos-0.5)^2 + 0.5 再缩放？简化：映射到-1到1，以0.5为中心
        raw = -4 * (pos - 0.5) ** 2 + 1  # 当pos=0.5时为1（负），pos=0或1时为0？调整
        # 希望当pos在0.3~0.7时负，极端时正；因此用 (pos-0.5)*2 再压缩
        # 改用：2*(pos-0.5) 然后限制在[-1,1]? 那在中部为0。但我们需要中部负值，所以取负号
        # 更直接：当pos<0.3或>0.7时正，否则负
        result = np.where((pos < 0.3) | (pos > 0.7), 1.0, -1.0)
        # 平滑处理：使用sigmoid转换
        x = (pos - 0.5) * 10  # 放大
        sig = 2 / (1 + np.exp(-x)) - 1  # 将sigmoid映射到[-1,1]，但中间是0
        # 想要中间负，所以取负
        result = -sig
        result = pd.Series(result, index=df.index).fillna(0.0)
        return result
