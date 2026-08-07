"""AI因子: 信号冲突指数 | 置信:65% | 基于短期均线位置与RSI方向的一致性，当两者冲突时输出接近0，一致时输出偏向±1。适用于识别regime=unknown下的噪音环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class SignalConflictIndex(BaseFactor):
    """基于短期均线位置与RSI方向的一致性，当两者冲突时输出接近0，一致时输出偏向±1。适用于识别regime=unknown下的噪音环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_conflict",
            name="Signal Conflict Index",
            display_name="信号冲突指数",
            description="基于短期均线位置与RSI方向的一致性，当两者冲突时输出接近0，一致时输出偏向±1。适用于识别regime=unknown下的噪音环境。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算5日均线和20日均线
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        # 价格相对于均线的位置
        pos_ma5 = (close - ma5) / ma5
        pos_ma20 = (close - ma20) / ma20
        # 计算RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        # 标准化RSI到[-1,1]
        rsi_norm = (rsi - 50) / 50
        # 两个信号：均线位置方向（取ma5与ma20差值）
        ma_diff = (ma5 - ma20) / close
        # 信号一致性：如果两个信号同号则取均值，否则取0
        sign1 = np.sign(pos_ma5.fillna(0))
        sign2 = np.sign(rsi_norm.fillna(0))
        conflict_flag = (sign1 == sign2).astype(float)
        # 当冲突时返回0，否则返回组合信号
        combined = (pos_ma5.fillna(0) + rsi_norm.fillna(0)) / 2
        result = combined * conflict_flag
        # 确保在[-1,1]
        result = result.clip(-1, 1)
        return result
