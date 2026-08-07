"""AI因子: 趋势疲弱因子 | 置信:65% | 基于短期均线与长期均线的偏离度及成交量确认，当偏离度小且成交量萎缩时，表明趋势不明朗，容易导致持仓超时亏损，信号为负。使用sigmoid映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class trend_weakness(BaseFactor):
    """基于短期均线与长期均线的偏离度及成交量确认，当偏离度小且成交量萎缩时，表明趋势不明朗，容易导致持仓超时亏损，信号为负。使用sigmoid映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trdwk",
            name="trend_weakness",
            display_name="趋势疲弱因子",
            description="基于短期均线与长期均线的偏离度及成交量确认，当偏离度小且成交量萎缩时，表明趋势不明朗，容易导致持仓超时亏损，信号为负。使用sigmoid映射到[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算均线
        ma20 = data['close'].rolling(20).mean()
        ma50 = data['close'].rolling(50).mean()
        # 偏离度（标准化价格）
        price = data['close']
        div = (ma20 - ma50) / (ma50 + 1e-8)
        # 成交量比：当前成交量相对20日均值
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma20 + 1e-8)
        # 综合信号：偏离度绝对值小且成交量低迷时，信号为负
        raw = - (np.abs(div) * (1 - vol_ratio.clip(0,1))) 
        # 用tanh映射到[-1,1]
        result = np.tanh(raw * 5)  # 缩放因子
        return result.fillna(0)
