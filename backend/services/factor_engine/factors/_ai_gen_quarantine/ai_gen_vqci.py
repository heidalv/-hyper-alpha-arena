"""AI因子: 成交量质量确认指数 | 置信:55% | 检验价格变动是否得到成交量支撑，避免无量空涨/跌。通过价格变动方向与成交量变化的背离程度，结合成交量相对于均量的偏离，输出趋势可信度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeQualityConfirmationIndex(BaseFactor):
    """检验价格变动是否得到成交量支撑，避免无量空涨/跌。通过价格变动方向与成交量变化的背离程度，结合成交量相对于均量的偏离，输出趋势可信度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vqci",
            name="Volume Quality Confirmation Index",
            display_name="成交量质量确认指数",
            description="检验价格变动是否得到成交量支撑，避免无量空涨/跌。通过价格变动方向与成交量变化的背离程度，结合成交量相对于均量的偏离，输出趋势可信度。",
            category="volume",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 价格变化率
        price_ret = data['close'].pct_change(5)
        # 成交量变化：当前 volume 与 20日均量之比
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma20 + 1e-10)
        # 价格方向与成交量方向的协同：如果价格涨且量增，信号强；价格涨但量缩，信号弱
        # 定义价格方向
        price_sign = np.sign(price_ret)
        # 成交量方向：量比减去1
        vol_deviation = vol_ratio - 1.0
        # 协同得分：price_sign * vol_deviation，但如果price_sign为0则0
        raw_score = price_sign * vol_deviation
        # 使用atanh-like映射到[-1,1]：用sigmoid缩放
        factor = 2 / (1 + np.exp(-raw_score)) - 1
        # 处理缺失值
        factor = factor.fillna(0)
        # 平滑一下
        factor = factor.rolling(3).mean().fillna(0)
        return factor.clip(-1, 1)
