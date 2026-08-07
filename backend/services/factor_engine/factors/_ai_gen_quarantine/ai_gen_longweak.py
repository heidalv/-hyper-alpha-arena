"""AI因子: 多头弱势指数因子 | 置信:65% | 综合多个短期指标判断做多风险。使用价格相对低位、成交量萎缩和波动率上升的复合信号，当三者同时出现时认为市场状态不明确（regime=unknown），应避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Long_Weakness_Index(BaseFactor):
    """综合多个短期指标判断做多风险。使用价格相对低位、成交量萎缩和波动率上升的复合信号，当三者同时出现时认为市场状态不明确（regime=unknown），应避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_longweak",
            name="Long_Weakness_Index",
            display_name="多头弱势指数因子",
            description="综合多个短期指标判断做多风险。使用价格相对低位、成交量萎缩和波动率上升的复合信号，当三者同时出现时认为市场状态不明确（regime=unknown），应避免做多。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 价格位置：接近近期低点 (当前close在20日区间的位置)
        rolling_low = data['low'].rolling(20).min()
        rolling_high = data['high'].rolling(20).max()
        price_pos = (data['close'] - rolling_low) / (rolling_high - rolling_low).replace(0, np.nan)
        price_pos = 1 - price_pos  # 越接近低点越大
        # 成交量萎缩：当前量小于20日均量
        vol_ma = data['volume'].rolling(20).mean().replace(0, np.nan)
        vol_shrink = 1 - (data['volume'] / vol_ma)
        vol_shrink = vol_shrink.clip(0, 1)
        # 波动率上升：ATR相对前一日的增长率
        high_low = data['high'] - data['low']
        high_close = (data['high'] - data['close'].shift(1)).abs()
        low_close = (data['low'] - data['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(10).mean()
        atr_change = atr.pct_change(5)
        vol_up = atr_change.clip(0, None) / 0.5  # 假设最大增长50%为1
        vol_up = vol_up.clip(0, 1)
        # 综合信号
        raw = (price_pos * 0.5 + vol_shrink * 0.3 + vol_up * 0.2)
        # 映射到[-1,1]，大于0.5视为负向
        result = 1 - 2 * raw
        return result.fillna(0)
