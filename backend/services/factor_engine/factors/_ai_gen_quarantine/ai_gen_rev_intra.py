"""AI因子: 日内反转强度 | 置信:55% | 衡量开盘价与当前价格的关系，结合日内波动率，当价格在低波动环境下快速反向时给出负值，预测类似模式中的止损风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class IntradayReversalIntensity(BaseFactor):
    """衡量开盘价与当前价格的关系，结合日内波动率，当价格在低波动环境下快速反向时给出负值，预测类似模式中的止损风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_intra",
            name="Intraday Reversal Intensity",
            display_name="日内反转强度",
            description="衡量开盘价与当前价格的关系，结合日内波动率，当价格在低波动环境下快速反向时给出负值，预测类似模式中的止损风险。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 输入: data DataFrame with columns open, high, low, close, volume
        # 日内价格相对位置
        rel_pos = (data['close'] - data['open']) / (data['high'] - data['low'] + 1e-10)
        # 日内波动率 (ATR-like)
        atr = (data['high'] - data['low']).rolling(5).mean()
        norm_atr = atr / data['close'].rolling(5).mean()
        # 反转信号: 当rel_pos接近极端且波动率低时，未来容易反向
        extreme = (rel_pos.abs() > 0.8).astype(int)
        low_vol = (norm_atr < norm_atr.rolling(20).quantile(0.3)).astype(int)
        # 方向: 若rel_pos>0.8 (高位)则负向，若<-0.8 (低位)则正向
        direction = np.where(rel_pos > 0.8, -1, np.where(rel_pos < -0.8, 1, 0))
        result = direction * extreme * low_vol
        # 转换为[-1,1]连续值
        result = result * (1 - norm_atr / norm_atr.rolling(20).max().replace(0, np.nan))
        result = result.clip(-1, 1)
        return result.fillna(0)
