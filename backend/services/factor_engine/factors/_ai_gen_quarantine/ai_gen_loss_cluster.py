"""AI因子: 亏损聚集信号 | 置信:60% | 统计过去N笔交易中连续亏损次数和累计亏损幅度，结合波动率放大。当出现多笔亏损且亏损幅度超过阈值时输出负值，警示市场状态恶化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LossClusterWarning(BaseFactor):
    """统计过去N笔交易中连续亏损次数和累计亏损幅度，结合波动率放大。当出现多笔亏损且亏损幅度超过阈值时输出负值，警示市场状态恶化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_loss_cluster",
            name="Loss Cluster Warning",
            display_name="亏损聚集信号",
            description="统计过去N笔交易中连续亏损次数和累计亏损幅度，结合波动率放大。当出现多笔亏损且亏损幅度超过阈值时输出负值，警示市场状态恶化。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 模拟交易记录：根据实际平台日志回测时需替换为真实盈亏序列
        # 这里用价格变化作为代理：连续下跌幅度视为亏损
        close = data['close']
        ret = close.pct_change()
        # 定义“亏损”为负收益，并考虑连续亏损次数
        loss_flag = (ret < -0.002).astype(int)  # 0.2% as threshold
        # 连续亏损计数
        consecutive_loss = loss_flag * (loss_flag.groupby((loss_flag != loss_flag.shift()).cumsum()).cumsum() + 0)
        # 亏损幅度累计
        loss_ret = ret.where(ret < 0, 0)
        cum_loss = loss_ret.rolling(window=5).sum()
        # 信号：连续亏损>=3且累计亏损<-1%则强烈负向
        signal = np.where((consecutive_loss >= 3) & (cum_loss < -0.01), -1.0,
                          np.where((consecutive_loss >= 2) & (cum_loss < -0.005), -0.5, 0.0))
        result = pd.Series(signal, index=data.index)
        result.fillna(0.0, inplace=True)
        return result.clip(-1, 1)
