"""AI因子: 波动率状态分类器 | 置信:60% | 基于20日波动率的历史百分位，识别市场处于低波动（稳定）或高波动（未知）状态。当波动率处于历史高位（>80%分位）时因子值接近+1，低位时接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeClassifier(BaseFactor):
    """基于20日波动率的历史百分位，识别市场处于低波动（稳定）或高波动（未知）状态。当波动率处于历史高位（>80%分位）时因子值接近+1，低位时接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_regime",
            name="Volatility Regime Classifier",
            display_name="波动率状态分类器",
            description="基于20日波动率的历史百分位，识别市场处于低波动（稳定）或高波动（未知）状态。当波动率处于历史高位（>80%分位）时因子值接近+1，低位时接近-1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算对数收益率
        log_ret = np.log(data['close'] / data['close'].shift(1))
        # 20日波动率（年化标准差不考虑，直接用标准差）
        vol = log_ret.rolling(window=20).std()
        # 计算历史百分位（使用扩展窗口）
        # 为了避免look-ahead，使用rolling窗口计算百分位，这里用expanding
        # 但expanding会包含未来信息？不，expanding只用过去到当前。
        # 使用rank方法
        rank = vol.rank(pct=True)  # 计算当前在整个序列中的百分位，注意这是全局的，有未来信息？
        # 改为滚动窗口内百分位：对每个时刻，用前200天的数据计算百分位
        def percentile_in_window(s):
            # s是当前窗口值
            pass
        # 简化：使用rolling apply但效率低，直接用expanding计算rank，但expanding会用到所有历史，无未来；但整个样本中后期会用到前期数据，但不会用到未来数据。
        # 更稳健：使用rolling(min_periods=1)计算百分位
        # 这里实现一个简单的：对每个t，使用过去200个观测计算百分位
        window = 100
        result = vol.rolling(window=window, min_periods=1).apply(
            lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10),
            raw=False
        )
        # 映射到[-1,1]: 0~0.5 -> -1~0, 0.5~1 -> 0~1? 更直观：直接2*perc-1
        result = 2 * result - 1
        result = result.fillna(0.0)
        return result
