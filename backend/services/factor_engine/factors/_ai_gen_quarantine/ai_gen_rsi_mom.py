"""AI因子: RSI动量过滤因子 | 置信:60% | 使用RSI的动量变化率，结合价格方向，判断当前超买超卖区域的动能持续性。当RSI从超卖区域向上突破且价格持续新高时为正，反之为负。用于过滤多空信号，避免在RSI钝化或背离时开仓，减少无效止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSIMomentumFilter(BaseFactor):
    """使用RSI的动量变化率，结合价格方向，判断当前超买超卖区域的动能持续性。当RSI从超卖区域向上突破且价格持续新高时为正，反之为负。用于过滤多空信号，避免在RSI钝化或背离时开仓，减少无效止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_mom",
            name="RSI Momentum Filter",
            display_name="RSI动量过滤因子",
            description="使用RSI的动量变化率，结合价格方向，判断当前超买超卖区域的动能持续性。当RSI从超卖区域向上突破且价格持续新高时为正，反之为负。用于过滤多空信号，避免在RSI钝化或背离时开仓，减少无效止损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        rsi_period = 14
        mom_period = 5
        # 计算RSI
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(rsi_period).mean()
        avg_loss = loss.rolling(rsi_period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # RSI的动量（变化率）
        rsi_mom = rsi.diff(mom_period)
        # 价格方向：当前close与mom_period前close的差值
        price_dir = data['close'] - data['close'].shift(mom_period)
        # 组合信号：rsi_mom和price_dir同向则强化，反向则弱化
        comb = rsi_mom * price_dir / (data['close'] + 1e-10) * 100
        # 用tanh压缩到[-1,1]
        result = np.tanh(comb)
        return result.fillna(0.0)
