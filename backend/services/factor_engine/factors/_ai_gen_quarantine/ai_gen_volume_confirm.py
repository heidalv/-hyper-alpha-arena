"""AI因子: 成交量确认因子 | 置信:60% | 通过价格变动与成交量的相关性判断价格变动的可信度。当价格上升时成交量放大（正相关）则确认趋势，因子为正；当价格变动与成交量背离（如缩量上涨）则预示潜在反转，因子为负。综合短期相关系数给出稳健信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Confirmation_Factor(BaseFactor):
    """通过价格变动与成交量的相关性判断价格变动的可信度。当价格上升时成交量放大（正相关）则确认趋势，因子为正；当价格变动与成交量背离（如缩量上涨）则预示潜在反转，因子为负。综合短期相关系数给出稳健信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_confirm",
            name="Volume Confirmation Factor",
            display_name="成交量确认因子",
            description="通过价格变动与成交量的相关性判断价格变动的可信度。当价格上升时成交量放大（正相关）则确认趋势，因子为正；当价格变动与成交量背离（如缩量上涨）则预示潜在反转，因子为负。综合短期相关系数给出稳健信号。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算对数收益率
        ret = np.log(close / close.shift(1))
        vol_change = np.log(volume / volume.shift(1))
        # 过去5日滚动相关系数
        corr = ret.rolling(5).corr(vol_change)
        # 处理缺失值并用tanh压缩到[-1,1]
        result = np.tanh(corr * 3)
        # 当corr为正且价格上升时为正，反之为负
        # 但corr本身已经包含方向信息：正相关表示量价同向，负相关表示背离
        return result.fillna(0)
