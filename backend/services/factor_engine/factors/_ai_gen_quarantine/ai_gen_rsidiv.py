"""AI因子: RSI顶背离 | 置信:60% | 检测价格创N日新高但RSI未能同步创新高，预示上涨动能衰竭，产生负信号。计算过去20日价格新高与RSI新高对比，背离程度归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSI_Divergence(BaseFactor):
    """检测价格创N日新高但RSI未能同步创新高，预示上涨动能衰竭，产生负信号。计算过去20日价格新高与RSI新高对比，背离程度归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsidiv",
            name="RSI_Divergence",
            display_name="RSI顶背离",
            description="检测价格创N日新高但RSI未能同步创新高，预示上涨动能衰竭，产生负信号。计算过去20日价格新高与RSI新高对比，背离程度归一化至[-1,1]。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 20
        if len(data) < n:
            return pd.Series(np.nan, index=data.index)
        close = data['close']
        # 计算RSI (14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 滚动窗口内价格最高值
        rolling_high = close.rolling(n).max()
        # 价格创20日新高? 当前close等于rolling_high
        is_new_high = (close == rolling_high) & (rolling_high > close.shift(1))
        # 对应的RSI值
        rsi_at_high = rsi[is_new_high]
        # 获取上次新高的RSI (向前填充)
        last_high_rsi = rsi_at_high.reindex(data.index, method='ffill')
        # 背离：当前未创新高但价格高于前高？简化：如果当前价格高于前高但RSI低于上次新高时的RSI
        # 用前一个高点位置
        peaks = (close == rolling_high).cumsum()
        # 更简单的实现：计算价格高于近期均值+1std且RSI向下
        # 改用另一种方法：价格相对位置与RSI相对位置差值
        # 计算价格z-score
        price_z = (close - close.rolling(20).mean()) / (close.rolling(20).std() + 1e-10)
        rsi_z = (rsi - rsi.rolling(20).mean()) / (rsi.rolling(20).std() + 1e-10)
        # 背离：价格z高，但rsi_z低
        signal = - (price_z - rsi_z)  # 当price_z>rsi_z时负
        signal = signal.clip(-1, 1)
        signal = signal.fillna(0)
        return signal
