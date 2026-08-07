"""
D7: WorldQuant 101 Formulaic Alphas — 20个代表性因子

来源: WorldQuant 101 Formulaic Alphas (2015)
适配: 从股票日频公式 → 加密货币分钟级数据

每个Alpha继承BaseFactor，实现 calculate() 和 get_metadata()。
FactorLoader.discover_and_load_all() 会自动扫描并注册。
"""

import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


# ════════════════════════════════════════════════════════════
# Alpha #001: (rank(Ts_ArgMax(SignedPower((returns<0?stddev:close),2.),5))-0.5)
# 含义: 近期最大波动方向
# ════════════════════════════════════════════════════════════
class WQ_Alpha001(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha001",
            name="MaxVolDirection",
            display_name="WQ#001 最大波动方向",
            description="近期最大波动方向的排名信号",
            category="composite",
            subcategory="volatility",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=5,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change()
        cond = np.where(returns < 0, data['close'].rolling(20).std(), data['close'])
        series = pd.Series(cond, index=data.index)
        return (series.rolling(5).apply(lambda x: x.argmax()).rank(pct=True) - 0.5).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #002: (-1 * correlation(rank(delta(log(volume),2)), rank(((close-open)/open)),6))
# 含义: 量价背离 — 放量不涨 = 看空
# ════════════════════════════════════════════════════════════
class WQ_Alpha002(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha002",
            name="VolumePriceDivergence",
            display_name="WQ#002 量价背离",
            description="量增价不涨=看空信号",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=6,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        log_vol = np.log(data['volume'] + 1e-9)
        delta_vol = log_vol.diff(2)
        o2c = (data['close'] - data['open']) / (data['open'] + 1e-9)
        return -(delta_vol.rank(pct=True).rolling(6)
                  .corr(o2c.rank(pct=True))).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #003: (-1 * correlation(rank(open), rank(volume), 10))
# 含义: 开盘价与成交量负相关 → 吸筹信号
# ════════════════════════════════════════════════════════════
class WQ_Alpha003(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha003",
            name="OpenVolumeCorr",
            display_name="WQ#003 开盘量价相关",
            description="开盘价与成交量负相关=吸筹",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=10,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        rank_open = data['open'].rank(pct=True)
        rank_vol = data['volume'].rank(pct=True)
        return -(rank_open.rolling(10).corr(rank_vol)).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #006: (-1 * correlation(open, volume, 10))
# 含义: 与003类似但用原始值
# ════════════════════════════════════════════════════════════
class WQ_Alpha006(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha006",
            name="RawOpenVolumeCorr",
            display_name="WQ#006 原始开盘量相关",
            description="开盘价与成交量原始值负相关",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=10,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return -(data['open'].rolling(10).corr(data['volume'])).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #009: ((0 < ts_min(delta(close,1),5)) ? delta(close,1) : ...)
# 含义: 连续上涨动量（简化版）
# ════════════════════════════════════════════════════════════
class WQ_Alpha009(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha009",
            name="ConsecutiveMomentum",
            display_name="WQ#009 连续动量",
            description="5日连涨动量信号",
            category="technical",
            subcategory="momentum",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=5,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        delta_close = data['close'].diff()
        cond = delta_close.rolling(5).min() > 0
        result = pd.Series(np.where(cond, delta_close, 0), index=data.index)
        return result.rolling(5).mean().fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #012: (sign(delta(volume,1)) * (-1 * delta(close,1)))
# 含义: 放量下跌=强化看空
# ════════════════════════════════════════════════════════════
class WQ_Alpha012(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha012",
            name="VolumeAmplifiedMove",
            display_name="WQ#012 放量方向强化",
            description="量变化方向×价格变化=强化信号",
            category="technical",
            subcategory="momentum",
            version="2.0.0",
            author="WorldQuant (adapted)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        vol_sign = np.sign(data['volume'].diff())
        price_delta = -data['close'].diff()
        return (vol_sign * price_delta).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #016: (-1 * rank(covariance(rank(close), rank(volume), 5)))
# 含义: 价量协方差为负=看多
# ════════════════════════════════════════════════════════════
class WQ_Alpha016(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha016",
            name="PriceVolCov",
            display_name="WQ#016 价量协方差",
            description="价量排名协方差信号",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=5,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        r_close = data['close'].rank(pct=True)
        r_vol = data['volume'].rank(pct=True)
        cov = r_close.rolling(5).cov(r_vol)
        return -(cov.rank(pct=True)).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #020: (-1 * rank(open - delay(high, 1)))
# 含义: 开盘低于昨日高点=缺口衰竭
# ════════════════════════════════════════════════════════════
class WQ_Alpha020(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha020",
            name="GapExhaustion",
            display_name="WQ#020 缺口衰竭",
            description="开盘低于昨日高点=缺口衰竭看空",
            category="technical",
            subcategory="trend",
            version="2.0.0",
            author="WorldQuant (adapted)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        gap = data['open'] - data['high'].shift(1)
        return -(gap.rank(pct=True)).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #023: (((sum(high,20)/20) < high) ? (-1*delta(high,2)) : 0)
# 含义: 突破20日高点均值=动量增强
# ════════════════════════════════════════════════════════════
class WQ_Alpha023(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha023",
            name="HighBreakout",
            display_name="WQ#023 突破高点均值",
            description="突破20日均高=动量看多",
            category="technical",
            subcategory="momentum",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=20,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma_high = data['high'].rolling(20).mean()
        cond = data['high'] > ma_high
        delta = -data['high'].diff(2)
        return pd.Series(np.where(cond, delta, 0), index=data.index).fillna(0)


# ════════════════════════════════════════════════════════════  
# Alpha #028: scale(((correlation(adv20, low, 5) + (high+low)/2)) - close)
# 含义: 量均线与价格偏离
# ════════════════════════════════════════════════════════════
class WQ_Alpha028(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha028",
            name="VolumeMeanDeviation",
            display_name="WQ#028 量均偏离",
            description="成交量均线与价格偏离信号",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=20,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        adv20 = data['volume'].rolling(20).mean()
        corr_vol_low = adv20.rolling(5).corr(data['low'])
        mid_price = (data['high'] + data['low']) / 2
        raw = (corr_vol_low + mid_price - data['close'])
        return (raw / raw.std()).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #032: (scale(((sum(close,7)/7)-close)) + (20*scale(corr(vwap,delay(close,5),230))))
# 含义: 均值回归 + 长期趋势确认
# ════════════════════════════════════════════════════════════
class WQ_Alpha032(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha032",
            name="MeanReversionTrend",
            display_name="WQ#032 均值回归趋势",
            description="短期均值回归+长期趋势确认",
            category="composite",
            subcategory="mean_reversion",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=230,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        vwap = (data['close'] * data['volume']).cumsum() / data['volume'].cumsum()
        ma7 = data['close'].rolling(7).mean()
        part1 = ma7 - data['close']
        part1 = part1 / part1.std() if part1.std() > 0 else part1
        part2 = vwap.rolling(230).corr(data['close'].shift(5))
        part2 = part2 / part2.std() if part2.std() > 0 else part2
        return (part1 + 20 * part2).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #038: (-1*rank(((sum(close,10)/10)-close)*corr(close,open,10)))
# 含义: 价格偏离均值且量价正相关=回调
# ════════════════════════════════════════════════════════════
class WQ_Alpha038(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha038",
            name="DeviationCorr",
            display_name="WQ#038 偏离相关",
            description="均值偏离×价量相关=回调信号",
            category="technical",
            subcategory="mean_reversion",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=10,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma10 = data['close'].rolling(10).mean()
        deviation = ma10 - data['close']
        corr_co = data['close'].rolling(10).corr(data['open'])
        return -((deviation * corr_co).rank(pct=True)).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #044: (-1*corr(high, rank(volume), 5))
# 含义: 高价放量=顶部信号
# ════════════════════════════════════════════════════════════
class WQ_Alpha044(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha044",
            name="HighVolumeCorr",
            display_name="WQ#044 高价放量相关",
            description="高价与放量正相关=顶部",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=5,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        r_vol = data['volume'].rank(pct=True)
        return -(data['high'].rolling(5).corr(r_vol)).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #049: (corr(rank(close), rank(volume60), 5) < ...)
# 含义: 价量相关为负=筹码转移
# ════════════════════════════════════════════════════════════
class WQ_Alpha049(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha049",
            name="ChipTransferSignal",
            display_name="WQ#049 筹码转移",
            description="价量负相关=筹码转移信号",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=60,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        r_close = data['close'].rank(pct=True)
        vol60 = data['volume'].rolling(60).mean()
        r_vol = vol60.rank(pct=True)
        return r_close.rolling(5).corr(r_vol).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #053: (-1 * delta((((close-low)-(high-close))/(close-low)), 9))
# 含义: 收盘位置变化 = 买卖压力转移
# ════════════════════════════════════════════════════════════
class WQ_Alpha053(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha053",
            name="ClosePositionDelta",
            display_name="WQ#053 收盘位置变化",
            description="日内收盘相对位置的变化率",
            category="technical",
            subcategory="momentum",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=9,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        spread = data['high'] - data['low'] + 1e-9
        pos = (data['close'] - data['low']) / spread - (data['high'] - data['close']) / spread
        return -(pos.diff(9)).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #057: sign(close-delay(close,1)) * -1*corr(close,low,9)
# 含义: 方向×价低相关=逆势信号
# ════════════════════════════════════════════════════════════
class WQ_Alpha057(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha057",
            name="ContrarianSignal",
            display_name="WQ#057 逆势信号",
            description="趋势与价低相关=趋势衰竭",
            category="technical",
            subcategory="contrarian",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=9,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        direction = np.sign(data['close'].diff())
        corr = data['close'].rolling(9).corr(data['low'])
        return (direction * -corr).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #064: (-1 * corr(open, volume, 60))
# 含义: 长周期开盘量价负相关 = 大资金入场
# ════════════════════════════════════════════════════════════
class WQ_Alpha064(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha064",
            name="LongTermOpenVolCorr",
            display_name="WQ#064 长周期开盘量相关",
            description="60周期开盘量价负相关=大资金",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=60,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return -(data['open'].rolling(60).corr(data['volume'])).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #068: ((high+low)/2 - delay((high+low)/2, 1)) * volume
# 含义: 中间价变化×成交量 = 资金流向
# ════════════════════════════════════════════════════════════
class WQ_Alpha068(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha068",
            name="MoneyFlowIndex",
            display_name="WQ#068 资金流向",
            description="中价变化×成交量=资金流向",
            category="technical",
            subcategory="volume",
            version="2.0.0",
            author="WorldQuant (adapted)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        mid = (data['high'] + data['low']) / 2
        return (mid.diff() * data['volume']).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #086: (close-delay(close,20))/delay(close,20)*100 - ts_rank(volume,20)
# 含义: 20周期收益 - 成交量排名 = 纯粹动量
# ════════════════════════════════════════════════════════════
class WQ_Alpha086(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha086",
            name="PureMomentum",
            display_name="WQ#086 纯粹动量",
            description="去除成交量噪音的20周期动量",
            category="technical",
            subcategory="momentum",
            version="2.0.0",
            author="WorldQuant (adapted)",
            lookback_period=20,
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ret20 = (data['close'] - data['close'].shift(20)) / data['close'].shift(20) * 100
        vol_rank = data['volume'].rolling(20).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1])
        return (ret20 - vol_rank).fillna(0)


# ════════════════════════════════════════════════════════════
# Alpha #101: ((close-open)/((high-low)+0.001))
# 含义: 日内效率指标
# ════════════════════════════════════════════════════════════
class WQ_Alpha101(BaseFactor):
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="wq_alpha101",
            name="IntradayEfficiency",
            display_name="WQ#101 日内效率",
            description="(收盘-开盘)/(最高-最低)=日内方向效率",
            category="technical",
            subcategory="momentum",
            version="2.0.0",
            author="WorldQuant (adapted)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        spread = data['high'] - data['low'] + 1e-9
        return ((data['close'] - data['open']) / spread).fillna(0)
