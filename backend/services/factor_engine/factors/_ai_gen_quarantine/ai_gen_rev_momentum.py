"""AI因子: 反转动量因子 | 置信:60% | 结合短期价格动量与相对强弱指标（RSI）的背离，识别潜在的反转信号。当价格创出新低（高）而RSI未创新低（高）时，视为反转信号。返回正值表示看多反转，负值表示看空反转，值域[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalMomentum(BaseFactor):
    """结合短期价格动量与相对强弱指标（RSI）的背离，识别潜在的反转信号。当价格创出新低（高）而RSI未创新低（高）时，视为反转信号。返回正值表示看多反转，负值表示看空反转，值域[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_momentum",
            name="Reversal Momentum",
            display_name="反转动量因子",
            description="结合短期价格动量与相对强弱指标（RSI）的背离，识别潜在的反转信号。当价格创出新低（高）而RSI未创新低（高）时，视为反转信号。返回正值表示看多反转，负值表示看空反转，值域[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 寻找价格极值与RSI背离
        # 使用rolling窗口寻找局部极值（以过去20根K线为窗口）
        window = 20
        # 最近最低价和最高价
        recent_low = low.rolling(window).min()
        recent_high = high.rolling(window).max()
        # 对应RSI在最低/最高位时的值 (用shift避免未来数据)
        rsi_at_low = rsi.shift()  # 简化: 用前一根RSI代表当前极值时的RSI? 这里采用更简单的方法：前20根RSI最小值
        rsi_min = rsi.rolling(window).min()
        rsi_max = rsi.rolling(window).max()
        # 当价格创新低且RSI未创新低 -> 看多反转
        buy_signal = (close == recent_low) & (rsi > rsi_min.shift(1))  # 当前RSI大于近期最低RSI
        # 当价格创新高且RSI未创新高 -> 看空反转
        sell_signal = (close == recent_high) & (rsi < rsi_max.shift(1))
        # 将信号归一化到[-1,1]
        result = pd.Series(0.0, index=data.index)
        result[buy_signal] = 1.0
        result[sell_signal] = -1.0
        # 使用价格变化幅度作为置信度加权（幅度越大信号越强）
        price_range = (high - low) / close * 100  # 百分比
        result = result * price_range.clip(0, 10) / 10.0  # 限制最大1
        return result
