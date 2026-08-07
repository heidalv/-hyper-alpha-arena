"""AI因子: 微观止损陷阱因子 | 置信:55% | 检测连续小实体阳线伴随成交量递减的诱多模式，这种模式常导致微小止损。计算过去K根K线中，每根阳线实体大小（close-open）与前一根相比的百分比变化，以及成交量变化。如果连续出现2根以上阳线实体缩小且成交量递减，则给出负向预警。具体：统计过去3根K线中阳线数量、实体变化方向和成交量变化方向，若阳线>=2且实体递减且成交量递减，则输出-1，否则0（或平滑）。最终输出-1/0/1的连续版本。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Stop-Loss Trap Pattern(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_micropattern", name="Micro Stop-Loss Trap Pattern",
        display_name="微观止损陷阱因子", description="检测连续小实体阳线伴随成交量递减的诱多模式，这种模式常导致微小止损。计算过去K根K线中，每根阳线实体大小（close-open）与前一根相比的百分比变化，以及成交量变化。如果连续出现2根以上阳线实体缩小且成交量递减，则给出负向预警。具体：统计过去3根K线中阳线数量、实体变化方向和成交量变化方向，若阳线>=2且实体递减且成交量递减，则输出-1，否则0（或平滑）。最终输出-1/0/1的连续版本。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 计算实体
    body = data['close'] - data['open']
    # 阳线标识
    is_bull = body > 0
    # 实体绝对值变化
    body_abs = np.abs(body)
    body_change = body_abs.pct_change()  # 相对于前一根的实体大小变化
    # 成交量变化
    vol_change = data['volume'].pct_change()
    # 构造信号：过去3根连续条件
    # 使用滚动函数
    def trap_signal(series):
        # series是包含is_bull, body_change, vol_change的窗口
        # 简化：取最近3根
        if len(series) < 3:
            return 0.0
        # 检查最近3根中阳线至少2根
        bulls = series['is_bull'].values[-3:]
        if sum(bulls) < 2:
            return 0.0
        # 取阳线对应的实体变化和成交量变化（只对阳线检查递减）
        # 检查连续阳线且实体递减且成交量递减
        # 简单：检查最后两根阳线
        last_two_bulls = [i for i in range(-2,0) if bulls[i+3]==True]  # 索引偏移
        if len(last_two_bulls) < 2:
            return 0.0
        # 比较最后两根阳线的body_change和vol_change（注意pct_change后第一根NaN）
        # 获取最后两根阳线的索引
        idx1 = -2
        idx2 = -1
        # 但idx1和idx2必须是阳线
        if not (series['is_bull'].iloc[idx1] and series['is_bull'].iloc[idx2]):
            return 0.0
        body1 = series['body_abs'].iloc[idx1]
        body2 = series['body_abs'].iloc[idx2]
        vol1 = series['volume'].iloc[idx1]
        vol2 = series['volume'].iloc[idx2]
        if body2 < body1 and vol2 < vol1:
            return -1.0
        else:
            return 0.0
    # 准备窗口数据
    df = pd.DataFrame({'is_bull': is_bull, 'body_abs': body_abs, 'volume': data['volume']})
    result = df.rolling(3).apply(trap_signal, raw=False)
    # 由于rolling返回的索引可能不对，改用shift方法
    # 另一种实现：直接循环
    # 简单：手动计算
    values = np.zeros(len(data))
    for i in range(2, len(data)):
        if is_bull.iloc[i-2] and is_bull.iloc[i-1] and is_bull.iloc[i]:
            # 连续三根阳线
            if (body_abs.iloc[i-1] < body_abs.iloc[i-2]) and (body_abs.iloc[i] < body_abs.iloc[i-1]) and \
               (data['volume'].iloc[i-1] < data['volume'].iloc[i-2]) and (data['volume'].iloc[i] < data['volume'].iloc[i-1]):
                values[i] = -1.0
        elif is_bull.iloc[i-1] and is_bull.iloc[i]:
            # 最后两根阳线
            if (body_abs.iloc[i] < body_abs.iloc[i-1]) and (data['volume'].iloc[i] < data['volume'].iloc[i-1]):
                values[i] = -1.0
        # 可以增加平滑：连续出现时信号更强烈，但这里简单处理
    result = pd.Series(values, index=data.index)
    return result
