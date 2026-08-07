"""AI因子: 流动性不足信号 | 置信:50% | 检测低流动性环境下的价格异常波动，模拟dust_cleanup或滑点大的情形。计算价格变动幅度与成交量的比率，并用波动率调整。当比率显著偏离正常范围时发出信号，正值表示异常上涨，负值表示异常下跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityDeficiencySignal(BaseFactor):
    """检测低流动性环境下的价格异常波动，模拟dust_cleanup或滑点大的情形。计算价格变动幅度与成交量的比率，并用波动率调整。当比率显著偏离正常范围时发出信号，正值表示异常上涨，负值表示异常下跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqd",
            name="Liquidity Deficiency Signal",
            display_name="流动性不足信号",
            description="检测低流动性环境下的价格异常波动，模拟dust_cleanup或滑点大的情形。计算价格变动幅度与成交量的比率，并用波动率调整。当比率显著偏离正常范围时发出信号，正值表示异常上涨，负值表示异常下跌。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格变动百分比
        pct_change = data['close'].pct_change()
        # 成交量（避免零值）
        vol = data['volume'] + 1e-8
        # 单位成交量的价格变动（价格冲击）
        impact = pct_change / vol
        # 滚动均值和标准差
        window = 20
        mean_impact = impact.rolling(window).mean()
        std_impact = impact.rolling(window).std()
        # Z-score，然后缩放到[-1,1]
        z = (impact - mean_impact) / (std_impact + 1e-8)
        # 使用tanh限制范围
        result = np.tanh(z / 3)
        return pd.Series(result, index=data.index)
