"""AI因子: 成交量确认因子 | 置信:55% | 衡量价格变动与成交量是否匹配。当价格上涨伴随放量（买方积极）或价格下跌伴随放量（卖方积极）时，方向可信度更高，返回正值；反之，价格变动却缩量时，方向可能虚假，返回负值。有助于规避无量反弹或无量下跌的陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConviction(BaseFactor):
    """衡量价格变动与成交量是否匹配。当价格上涨伴随放量（买方积极）或价格下跌伴随放量（卖方积极）时，方向可信度更高，返回正值；反之，价格变动却缩量时，方向可能虚假，返回负值。有助于规避无量反弹或无量下跌的陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_conviction",
            name="VolumeConviction",
            display_name="成交量确认因子",
            description="衡量价格变动与成交量是否匹配。当价格上涨伴随放量（买方积极）或价格下跌伴随放量（卖方积极）时，方向可信度更高，返回正值；反之，价格变动却缩量时，方向可能虚假，返回负值。有助于规避无量反弹或无量下跌的陷阱。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变动方向：当前close与前close比较
        price_change = close - close.shift(1)
        price_dir = np.sign(price_change)  # 1, -1, 0
        # 成交量变化：当前volume与过去5日均值比较
        vol_ma5 = volume.rolling(5).mean()
        vol_ratio = volume / vol_ma5  # 大于1表示放量
        # 量价配合得分：如果方向与放量一致则为正，否则为负
        # 使用tanh将ratio映射到[-1,1]附近，乘以方向
        vol_factor = np.tanh((vol_ratio - 1) * 3)
        score = price_dir * vol_factor
        # 处理缺失值和方向为0的情况
        score = score.fillna(0)
        return score.clip(-1, 1)
