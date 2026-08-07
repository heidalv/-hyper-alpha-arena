"""AI因子: 趋势一致性指数 | 置信:60% | 基于短期与长期移动平均线方向的一致性以及价格与均线偏离程度，判断当前市场是否处于清晰趋势状态。当短期均线与长期均线同向且价格偏离在合理范围内时，输出正值，表明趋势明确；反之输出负值，表示震荡或未知状态。旨在避免在regime=unknown时入场。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Consistency_Score_Index(BaseFactor):
    """基于短期与长期移动平均线方向的一致性以及价格与均线偏离程度，判断当前市场是否处于清晰趋势状态。当短期均线与长期均线同向且价格偏离在合理范围内时，输出正值，表明趋势明确；反之输出负值，表示震荡或未知状态。旨在避免在regime=unknown时入场。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tcsi",
            name="Trend Consistency Score Index",
            display_name="趋势一致性指数",
            description="基于短期与长期移动平均线方向的一致性以及价格与均线偏离程度，判断当前市场是否处于清晰趋势状态。当短期均线与长期均线同向且价格偏离在合理范围内时，输出正值，表明趋势明确；反之输出负值，表示震荡或未知状态。旨在避免在regime=unknown时入场。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算短期(10期)和长期(30期)均线
        ma_short = close.rolling(10).mean()
        ma_long = close.rolling(30).mean()
        # 均线方向：当前值相对前一期变化符号，取1或-1
        dir_short = np.sign(ma_short - ma_short.shift(1))
        dir_long = np.sign(ma_long - ma_long.shift(1))
        # 方向一致性：一致为1，不一致为-1
        consistency = np.where(dir_short == dir_long, 1, -1)
        # 价格偏离均线程度：使用ATR归一化
        tr = pd.DataFrame({'hl': high - low, 'hc': abs(high - close.shift(1)), 'lc': abs(low - close.shift(1))}).max(axis=1)
        atr = tr.rolling(14).mean()
        # 价格到MA_long的偏离百分比，除以ATR得到标准化偏离
        deviation = (close - ma_long) / atr
        # 偏离阈值，超出[-2,2]认为过度，降低信号强度
        deviation_clipped = np.clip(deviation, -2, 2) / 2
        # 合成信号：一致性乘以偏离信号，再通过tanh压缩到[-1,1]
        raw = consistency * deviation_clipped
        result = pd.Series(np.tanh(raw), index=close.index)
        return result.fillna(0.0)
