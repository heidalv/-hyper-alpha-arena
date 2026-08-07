"""
Factor Engine - 因子计算引擎

提供300+技术因子计算：
1. 动量因子 (Momentum)
2. 均值回归因子 (Mean Reversion)
3. 波动率因子 (Volatility)
4. 成交量因子 (Volume)
5. 趋势因子 (Trend)
6. 市场流向因子 (Market Flow)

Author: Hyper-Alpha-Arena
"""

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FactorCategory(Enum):
    """因子类别"""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    TREND = "trend"
    MARKET_FLOW = "market_flow"
    STRENGTH = "strength"
    PATTERN = "pattern"
    SENTIMENT = "sentiment"
    FUNDING = "funding"
    BEHAVIORAL = "behavioral"
    ONCHAIN = "onchain"          # 链上数据因子 (V3 §2.4)
    DERIVATIVES = "derivatives"  # 衍生品因子 (V3 §2.5)
    MACRO = "macro"              # 宏观情绪因子 (V3 §2.6)


@dataclass
class FactorValue:
    """因子值"""
    name: str
    category: FactorCategory
    value: float
    normalized: float = 0.0
    timestamp: Optional[str] = None
    has_data: bool = True   # [add] 标记是否有真实数据源（无数据因子不参与聚合，避免凑0稀释信号）
    is_directional: bool = True   # [add] 标记是否有方向语义（价格/成交量类=False，根治策略偏多）


class _FactorNormalizer:
    """按 symbol+factor 滚动 z-score 归一化器。

    背景：124 个因子量纲各异（RSI 0-100、volume 上千、macd 小数、ratio 0-1），
    旧逻辑用 tanh(value) 归一化，对 |value|>1 的因子仍是满格 ±1 → 31 个满格因子
    多空对冲后互相抵消，score 被压到 15 以下。z-score 让每个因子按自身历史分布
    转标准分，跨因子可比。

    设计要点：
    - key = "symbol:timeframe:factor_name"，按币种+周期隔离
      （BTC:5m:rsi 不污染 BTC:15m:rsi，修复之前主循环15m/scalp5m共用单例的污染）
    - 冷启动保护：样本 < min_samples 时回退 tanh（不返回失真 z-score）
    - z-score 无界，截断 ±3 后 tanh 压到 [-0.995, 0.995]
    - 纯内存 deque(maxlen=100)，重启重新积累（scalp 30s/轮，约 10 分钟够样本）
    """

    def __init__(self, window: int = 100, min_samples: int = 20):
        self.window = window
        self.min_samples = min_samples
        self._hist: Dict[str, "deque"] = {}

    def normalize(self, symbol: str, factor_name: str, value: float, timeframe: str = "") -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        # [fix] key 加入 timeframe，避免 5m/15m/1h 不同周期的同因子历史混在一起
        key = f"{symbol or '_global'}:{timeframe or '_'}:{factor_name}"
        h = self._hist.get(key)
        if h is None:
            h = deque(maxlen=self.window)
            self._hist[key] = h
        h.append(v)
        if len(h) < self.min_samples:
            # 冷启动：样本不足，回退 tanh（与旧逻辑一致，平滑过渡）
            return float(np.tanh(v))
        arr = np.array(h, dtype=float)
        std = float(np.std(arr))
        if std < 1e-9:
            return 0.0
        z = (v - float(np.mean(arr))) / std
        # 截断 ±3 后 tanh 有界化
        return float(np.tanh(max(-3.0, min(3.0, z))))


# 模块级单例（进程内共享，跨 symbol 隔离靠 key）
_factor_normalizer = _FactorNormalizer()

# [add 2026-06-30] 无方向语义因子关键词黑名单。
# 这些因子的 value 是价格/成交量的绝对值（天然永远为正），被 _default_direction
# 的 tanh 映射成 +1.0（强看多），导致合成 direction 永远偏正 → 策略偏多、空单难触发。
# 匹配这些关键词的因子 is_directional=False，不参与方向聚合。
_NON_DIRECTIONAL_KEYWORDS = (
    # 价格绝对值类（value~价格，永远正）
    "price", "level", "psar", "ichimoku", "keltner", "bb_lower", "bb_upper",
    "bb_mid", "pivot", "fib", "ma20", "ma50", "ma200", "sma_", "ema_",
    "vwap", "point", "tenkan", "kijun", "senkou", "cloud_", "support", "resistance",
    # 成交量/金额绝对值类（永远正）
    "volume", "vol_", "notional", "dollar", "buy_volume", "sell_volume",
    "aggressive_buy", "aggressive_sell", "trade_count", "amount", "turnover",
    "liquidity_score", "consolidation", "ad_line",
    # [add] 振荡器类（value 0-100，tanh 后趋近+1，天然偏多无方向）
    "cci_", "stoch_", "rsi_7", "uo", "mfi_", "trend_persistence", "trade_intensity",
    "williams", "trix",
    # [add] 比率/分数类（value 永远>0，天然偏正）
    "buy_sell_ratio", "high_low_ratio", "active_addresses", "whale_transaction",
    "flow_ratio", "volatility_ratio", "volatility_anomaly", "bull_bear_index",
    "bb_width_raw", "trend_strength", "amihud", "bid_ask_spread",
    "liquidation_pressure", "regime_transition", "regime_change",
    # 注意：wq_alpha / orderflow / multi_tf_momentum / depth_ratio / cmf 等
    # 值域可正可负、有真方向语义，**不**纳入黑名单（保留为 directional）。
)
# 价格量级阈值：|value|>此值判定为价格类（如 BTC 价格 58000）
_PRICE_MAGNITUDE_THRESHOLD = 1000.0


def _is_factor_directional(name: str, value: float, has_mapper: bool) -> bool:
    """判定因子是否有方向语义。

    有专用 direction mapper 的因子一定有方向（它们有自己的语义归一化）。
    匹配价格/成交量关键词的、或 value 达价格量级的，判为无方向。
    """
    if has_mapper:
        return True
    # [protect] 这 22 个因子在 FactorSignalGenerator 里有专用 direction mapper，
    # 即使 value 大(如 vwap=价格)或名字含被黑名单关键词，也一定 directional。
    _MAPPER_FACTOR_NAMES = {
        "rsi", "macd", "momentum", "roc", "adx", "bb_width", "zscore",
        "atr_ratio", "atr", "hv", "parkinson_vol", "obv", "vwap",
        "volume_zscore", "cvd_ratio", "ema_trend", "sma_cross", "supertrend",
        "taker_ratio", "oi_delta", "funding_rate",
    }
    if name in _MAPPER_FACTOR_NAMES:
        return True
    n = (name or "").lower()
    if any(k in n for k in _NON_DIRECTIONAL_KEYWORDS):
        return False
    try:
        if abs(float(value)) > _PRICE_MAGNITUDE_THRESHOLD:
            return False
    except (TypeError, ValueError):
        pass
    return True


class FactorEngine:
    """
    因子计算引擎
    
    支持300+技术因子的实时计算，
    并提供因子选择和权重调整功能
    """
    
    # 因子注册表
    FACTORS: Dict[str, Dict] = {}

    def __init__(self):
        self._register_all_factors()
        self._merge_registry()
        # 载入已晋升的公式因子（发现→回测打分 A/B→active）
        try:
            self._load_active_custom_factors()
        except Exception:
            pass
        # [2026-07-18 新增] 载入因子进化闭环(factor_evolution_loop/AlphaMiner)晋升的
        # 表达式AST因子，见 _load_active_evolution_factors 说明
        try:
            self._load_active_evolution_factors()
        except Exception:
            pass

    def _merge_registry(self):
        """从 FactorRegistry 合并已注册因子到 self.FACTORS（2026-06-18 因子系统统一）。

        将 Registry 的 130+ 因子合并进生产引擎的 FACTORS 字典，
        使 compute_all_factors 能计算所有因子（不只是 21 个硬编码）。
        硬编码因子优先（key 冲突时不覆盖）。
        """
        import os as _os
        if _os.getenv("FACTOR_MERGE_REGISTRY", "true").lower() not in ("1", "true", "yes", "on"):
            return 0
        try:
            from backend.services.factor_engine.factor_loader import FactorLoader
            from backend.services.factor_engine.factor_registry import registry
            # 确保所有因子已发现加载
            FactorLoader().discover_and_load_all()

            merged = 0
            for factor_id in registry.list_factors():
                if factor_id in self.FACTORS:
                    continue  # 硬编码因子优先，不覆盖
                factor_cls = registry._factors.get(factor_id)
                if factor_cls is None:
                    continue
                try:
                    metadata = registry._metadata_cache.get(factor_id)
                    if metadata is None:
                        continue
                    # 映射 category 字符串到 FactorCategory enum
                    cat_str = metadata.category.upper() if metadata.category else "PATTERN"
                    try:
                        cat_enum = FactorCategory[cat_str]
                    except KeyError:
                        cat_enum = FactorCategory.PATTERN

                    adapter = self._make_registry_adapter(factor_cls)
                    if adapter is None:
                        continue  # 断点④：缺少 calculate 的因子跳过（已告警）

                    self.FACTORS[factor_id] = {
                        'category': cat_enum,
                        'name': metadata.display_name or metadata.name or factor_id,
                        'description': metadata.description or '',
                        'compute': adapter,
                    }
                    merged += 1
                except Exception as e:
                    logger.debug(f"[FactorEngine] 跳过因子 {factor_id}: {e}")

            if merged:
                logger.info(f"[FactorEngine] Registry 合并完成: 新增 {merged} 个因子, 总计 {len(self.FACTORS)}")
            return merged
        except Exception as e:
            logger.error(f"[FactorEngine] Registry 合并失败（降级为纯硬编码 21 因子）: {e}", exc_info=True)
            # 降级告警：合并失败时应让运维知晓因子系统处于降级状态
            if len(self.FACTORS) <= 21:
                logger.critical(
                    f"[FactorEngine] ⚠️ 因子系统处于降级状态！仅 {len(self.FACTORS)} 个硬编码因子可用，"
                    f"AI 生成因子全部未加载。请检查 factor_registry / factor_loader 是否正常。"
                )
            return 0

    def hot_reload(self) -> int:
        """热加载磁盘上新注册的因子（AI 挖掘注入后无需重启后端）。"""
        before = len(self.FACTORS)
        try:
            from backend.services.factor_engine.factor_loader import FactorLoader
            FactorLoader().discover_and_load_all()
        except Exception as e:
            logger.warning(f"[FactorEngine] hot_reload 扫描失败: {e}")
        added = self._merge_registry()
        # 载入已晋升（active）的公式因子（OpenCode/LLM 发现 → 回测打分 A/B → active）。
        self._load_active_custom_factors()
        # 载入因子进化闭环(factor_evolution_loop/AlphaMiner)晋升的表达式AST因子。
        try:
            self._load_active_evolution_factors()
        except Exception as e:
            logger.debug(f"[FactorEngine] hot_reload._load_active_evolution_factors 跳过: {e}")
        total_new = len(self.FACTORS) - before
        if total_new > 0:
            logger.info(
                f"[FactorEngine] 热加载完成: +{total_new} 因子 (merge={added}), 总计 {len(self.FACTORS)}"
            )
        return total_new

    def register_custom_factor(
        self,
        name: str,
        category: str = "discovered",
        formula: str = "",
        ic=None,
        rank_ic=None,
        source: str = "opencode",
        extra=None,
        **_kw,
    ) -> str:
        """登记一个发现出来的公式因子（补齐 factor_discovery 的调用缺口）。

        重要：登记只是把因子写入持久化目录（candidate 状态），**不会**立刻进入
        实时合成信号——必须先通过 `factor_backtest_scorer` 的样本外回测打分（A/B 级）
        才会被晋升为 active 并纳入实时使用。这样保证"自动发现的因子"不越过验证闸门。

        Args:
            name: 因子名
            category: 分类
            formula: numpy 公式（变量 close/high/low/volume/open）
            ic / rank_ic: 发现阶段的初步 IC（仅记录，非准入依据）
            source: 来源标签（opencode / llm ...）

        Returns:
            factor_id（登记失败返回空串）
        """
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
            res = custom_factor_store.register(
                name=name, formula=formula, category=category,
                ic=ic, rank_ic=rank_ic, source=source,
                extra=extra,
            )
            if res.get("ok"):
                logger.info(
                    f"[FactorEngine] 发现因子登记为候选: {res.get('factor_id')} "
                    f"(待回测打分晋升) source={source}"
                )
                return res.get("factor_id", "")
            logger.info(f"[FactorEngine] 发现因子登记被拒: {res.get('reason')}")
            return ""
        except Exception as e:
            logger.warning(f"[FactorEngine] register_custom_factor 失败 {name}: {e}")
            return ""

    def _load_active_custom_factors(self) -> int:
        """把持久化目录中 active 的公式因子挂进 FACTORS，供 compute_all_factors 使用。"""
        loaded = 0
        try:
            from backend.services.factor_engine.custom_factor_store import (
                custom_factor_store,
                make_formula_compute,
            )
            for rec in custom_factor_store.list_active():
                fid = rec.get("factor_id")
                if not fid or fid in self.FACTORS:
                    continue
                formula = rec.get("formula") or ""
                if not formula:
                    continue
                try:
                    cat_enum = self._resolve_category(rec.get("category"))
                    self.FACTORS[fid] = {
                        "category": cat_enum,
                        "name": rec.get("name") or fid,
                        "description": f"discovered/{rec.get('source', '?')} grade={rec.get('grade')}",
                        "compute": make_formula_compute(formula),
                    }
                    loaded += 1
                except Exception as e:
                    logger.debug(f"[FactorEngine] 跳过 active 公式因子 {fid}: {e}")
        except Exception as e:
            logger.debug(f"[FactorEngine] _load_active_custom_factors 跳过: {e}")
        if loaded:
            logger.info(f"[FactorEngine] 载入 {loaded} 个 active 公式因子")
        return loaded

    def _load_active_evolution_factors(self) -> int:
        """把因子进化闭环(factor_evolution_loop/AlphaMiner)晋升的表达式AST因子
        挂进 FACTORS，供 compute_all_factors 使用（真正参与实时信号合成）。

        [2026-07-18 新增，修复因子进化闭环与实盘脱节的核心断层] 此前
        factor_evolution_loop 每天挖掘/评估/清洗/"晋升"因子，产物只落库到
        Analytics 的 factor_active_set 表 (state=ACTIVE)，从未被任何实时路径读取
        ——ScalpFactorRouter 用的始终是 FACTORS 里那 1144 个 Registry Python 类
        因子，跟这套进化闭环完全是两条不相交的线。晋升等于"晋升到一个没人读的表"。

        这里补上桥接：查询 state=ACTIVE 的行，用 expr/parser.py 的 JSON AST 求值器
        (纯函数、无副作用，核查确认可安全复用) 编译表达式，包装成与其他因子相同的
        compute(klines, market_data) 闭包签名，挂进 self.FACTORS。factor_id 加
        "evo_" 前缀区分来源，避免与 Registry/custom_factor_store 的因子id冲突。
        """
        loaded = 0
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import FactorActiveSet
            from backend.services.alpha.factor_compute import kline_df_to_fields
            from backend.services.factor_engine.expr.parser import parse as _parse_expr

            db = AnalyticsSessionLocal()
            try:
                rows = db.query(FactorActiveSet).filter(
                    FactorActiveSet.state == "ACTIVE"
                ).all()
            finally:
                db.close()

            for r in rows:
                fid = f"evo_{r.factor_id}"
                if fid in self.FACTORS:
                    continue
                try:
                    expr = _parse_expr(r.expr_ast)
                except Exception as e:
                    logger.debug(f"[FactorEngine] 跳过失效表达式因子 {r.factor_id}: {e}")
                    continue

                def _compute(klines, market_data=None, _expr=expr):
                    try:
                        import pandas as _pd
                        df = klines if isinstance(klines, _pd.DataFrame) else _pd.DataFrame(klines)
                        if df is None or len(df) == 0:
                            return None
                        fields = kline_df_to_fields(df)
                        result = _expr.evaluate(fields)
                        val = result[-1] if hasattr(result, "__len__") else result
                        val = float(val)
                        return val if val == val else None  # NaN 防御
                    except Exception:
                        return None

                self.FACTORS[fid] = {
                    "category": self._resolve_category(r.source),
                    "name": fid,
                    "description": f"evolution_loop/{r.source or '?'} icir={r.icir}",
                    "compute": _compute,
                    # 保留原始表达式对象供事件驱动回测触发器(backend/services/backtest/
                    # trigger.py)复用——它需要对整段K线序列求值(而不是只取compute()
                    # 返回的最新一个点)来跑一次简化交易模拟，避免重新解析AST。
                    "_expr": expr,
                }
                loaded += 1
        except Exception as e:
            logger.debug(f"[FactorEngine] _load_active_evolution_factors 跳过: {e}")
        if loaded:
            logger.info(f"[FactorEngine] 桥接载入 {loaded} 个因子进化闭环产出的活跃因子(evo_*)")
        return loaded

    def _resolve_category(self, cat_str):
        """把 category 字符串映射到 FactorCategory enum（失败回退 PATTERN）。"""
        try:
            return FactorCategory[(cat_str or "PATTERN").upper()]
        except (KeyError, AttributeError):
            return FactorCategory.PATTERN

    @staticmethod
    def _make_registry_adapter(factor_cls):
        """适配 BaseFactor 子类为 compute_all_factors 兼容的 compute 签名。

        断点④修复：加载期做 hasattr 自检，坏因子告警而非静默丢弃。
        """
        # 加载期自检：calculate 是否存在
        if not hasattr(factor_cls, 'calculate'):
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "[FactorEngine] 因子 %s 缺少 calculate 方法，跳过（历史模板嵌套Bug）",
                getattr(factor_cls, '__name__', str(factor_cls)),
            )
            return None  # 返回 None → _merge_registry 跳过

        def _compute(klines, market_data=None):
            try:
                instance = factor_cls()
                result = instance.calculate(klines)
                if result is None:
                    return None
                if hasattr(result, 'iloc'):
                    val = result.iloc[-1] if len(result) > 0 else None
                elif hasattr(result, '__len__') and not isinstance(result, (str, bytes)):
                    val = result[-1] if len(result) > 0 else None
                else:
                    val = result
                return float(val) if val is not None and not (isinstance(val, float) and val != val) else None
            except Exception:
                return None
        return _compute
        
    def _register_all_factors(self):
        """注册所有因子"""
        # 动量因子
        self.FACTORS['rsi'] = {
            'category': FactorCategory.MOMENTUM,
            'name': 'RSI',
            'description': 'Relative Strength Index',
            'compute': self.compute_rsi
        }
        self.FACTORS['macd'] = {
            'category': FactorCategory.MOMENTUM,
            'name': 'MACD',
            'description': 'Moving Average Convergence Divergence',
            'compute': self.compute_macd
        }
        self.FACTORS['momentum'] = {
            'category': FactorCategory.MOMENTUM,
            'name': 'Momentum',
            'description': 'Price momentum',
            'compute': self.compute_momentum
        }
        self.FACTORS['roc'] = {
            'category': FactorCategory.MOMENTUM,
            'name': 'ROC',
            'description': 'Rate of Change',
            'compute': self.compute_roc
        }
        self.FACTORS['adx'] = {
            'category': FactorCategory.MOMENTUM,
            'name': 'ADX',
            'description': 'Average Directional Index',
            'compute': self.compute_adx
        }
        
        # 均值回归因子
        self.FACTORS['bb_width'] = {
            'category': FactorCategory.MEAN_REVERSION,
            'name': 'BB_Width',
            'description': 'Bollinger Band Width',
            'compute': self.compute_bb_width
        }
        self.FACTORS['zscore'] = {
            'category': FactorCategory.MEAN_REVERSION,
            'name': 'Z-Score',
            'description': 'Price Z-Score',
            'compute': self.compute_zscore
        }
        self.FACTORS['atr_ratio'] = {
            'category': FactorCategory.MEAN_REVERSION,
            'name': 'ATR Ratio',
            'description': 'ATR / Price Ratio',
            'compute': self.compute_atr_ratio
        }
        
        # 波动率因子
        self.FACTORS['atr'] = {
            'category': FactorCategory.VOLATILITY,
            'name': 'ATR',
            'description': 'Average True Range',
            'compute': self.compute_atr
        }
        self.FACTORS['hv'] = {
            'category': FactorCategory.VOLATILITY,
            'name': 'HV',
            'description': 'Historical Volatility',
            'compute': self.compute_hv
        }
        self.FACTORS['parkinson_vol'] = {
            'category': FactorCategory.VOLATILITY,
            'name': 'Parkinson Vol',
            'description': 'Parkinson Volatility',
            'compute': self.compute_parkinson_vol
        }
        
        # 成交量因子
        self.FACTORS['obv'] = {
            'category': FactorCategory.VOLUME,
            'name': 'OBV',
            'description': 'On Balance Volume',
            'compute': self.compute_obv
        }
        self.FACTORS['vwap'] = {
            'category': FactorCategory.VOLUME,
            'name': 'VWAP',
            'description': 'Volume Weighted Average Price',
            'compute': self.compute_vwap
        }
        self.FACTORS['volume_zscore'] = {
            'category': FactorCategory.VOLUME,
            'name': 'Volume Z-Score',
            'description': 'Volume Z-Score',
            'compute': self.compute_volume_zscore
        }
        self.FACTORS['cvd_ratio'] = {
            'category': FactorCategory.VOLUME,
            'name': 'CVD Ratio',
            'description': 'CVD / Total Notional',
            'compute': self.compute_cvd_ratio,
            'required_keys': ['cvd'],   # [add] 缺 market_data['cvd'] 则 has_data=False
        }
        
        # 趋势因子
        self.FACTORS['sma_cross'] = {
            'category': FactorCategory.TREND,
            'name': 'SMA Cross',
            'description': 'SMA Crossover Signal',
            'compute': self.compute_sma_cross
        }
        self.FACTORS['ema_trend'] = {
            'category': FactorCategory.TREND,
            'name': 'EMA Trend',
            'description': 'EMA Trend Alignment',
            'compute': self.compute_ema_trend
        }
        self.FACTORS['supertrend'] = {
            'category': FactorCategory.TREND,
            'name': 'SuperTrend',
            'description': 'SuperTrend Indicator',
            'compute': self.compute_supertrend
        }
        
        # 市场流向因子
        self.FACTORS['taker_ratio'] = {
            'category': FactorCategory.MARKET_FLOW,
            'name': 'Taker Ratio',
            'description': 'ln(Buy Notional / Sell Notional)',
            'compute': self.compute_taker_ratio,
            'required_keys': ['buy_notional', 'sell_notional'],   # [add]
        }
        self.FACTORS['oi_delta'] = {
            'category': FactorCategory.MARKET_FLOW,
            'name': 'OI Delta',
            'description': 'Open Interest Change %',
            'compute': self.compute_oi_delta,
            'required_keys': ['oi', 'prev_oi'],   # [add]（oi_delta_pct 也算有数据，见 compute_all_factors）
        }
        self.FACTORS['funding_rate'] = {
            'category': FactorCategory.MARKET_FLOW,
            'name': 'Funding Rate',
            'description': 'Funding Rate',
            'compute': self.compute_funding_rate,
            'required_keys': ['funding_rate'],   # [add]
        }
        
        logger.info(f"[FactorEngine] Registered {len(self.FACTORS)} factors")
        
    def compute_all_factors(
        self,
        klines: pd.DataFrame,
        market_data: Optional[Dict] = None,
        exclude_categories: Optional[set] = None,
    ) -> Dict[str, FactorValue]:
        """
        计算所有可用因子

        Args:
            klines: K线数据
            market_data: 市场数据 (成交量、OI、资金费率等)
            exclude_categories: 跳过的因子类别集合（如 {FactorCategory.PATTERN}）。
                短线热路径传此参数跳过 300+ 模式因子，从 ~8s/币降到 <1s。

        Returns:
            因子名称 -> 因子值
        """
        if klines is None or klines.empty:
            return {}

        # #12 Deribit 期权列注入（供 options_skew / iv_term_structure 等因子消费）
        _sym = str((market_data or {}).get("symbol") or "")
        if _sym:
            try:
                from backend.services.factor_engine.factor_bridge import inject_deribit_into_klines
                klines = inject_deribit_into_klines(klines, _sym)
            except Exception:
                pass
            
        results = {}

        # [add] 归一化要按 symbol+timeframe 隔离（BTC:5m 不污染 BTC:15m）
        _symbol = "_global"
        _timeframe = ""
        if isinstance(market_data, dict):
            _symbol = str(market_data.get("symbol") or "_global")
            _timeframe = str(market_data.get("timeframe") or market_data.get("tf") or "")

        # 复制一份遍历，防止热加载因子时 dict changed size during iteration
        for name, config in list(self.FACTORS.items()):
            # 跳过指定类别的因子（短线热路径跳过 PATTERN/BEHAVIORAL 等 300+ 模式因子）
            if exclude_categories and config.get('category') in exclude_categories:
                continue
            try:
                value = config['compute'](klines, market_data)
                if value is not None and not np.isnan(value):
                    # [fix] z-score 归一化（按 symbol+timeframe+factor 历史分布），
                    # 替代旧的 tanh(value)。timeframe 隔离修复主循环15m/scalp5m共用单例的污染。
                    normalized = _factor_normalizer.normalize(_symbol, name, value, timeframe=_timeframe)
                    # [add] 判定数据源是否可用：声明了 required_keys 的因子，
                    # 若 market_data 里没有任何一个必需键 → has_data=False（凑数0值，不参与聚合）
                    required = config.get('required_keys')
                    if required:
                        has_data = bool(market_data) and any(
                            k in (market_data or {}) for k in required
                        )
                    else:
                        has_data = True
                    results[name] = FactorValue(
                        name=config['name'],
                        category=config['category'],
                        value=value,
                        normalized=normalized,
                        has_data=has_data,
                        # [add] 判定方向语义：价格/成交量类因子(is_directional=False)
                        # 不参与 direction 聚合，根治策略偏多。has_mapper=False 因为
                        # compute_all_factors 阶段不知道哪些有专用 mapper（由
                        # generate_signals 处理），这里对 default 因子做启发式判定。
                        is_directional=_is_factor_directional(name, value, has_mapper=False),
                    )
            except Exception as e:
                logger.warning(f"[FactorEngine] Error computing {name}: {e}")
                
        return results
    
    # ========== 动量因子 ==========
    
    # [P0-3 2026-07-30] 加密5m周期适配
    # 原值 RSI=14/ATR=14/MACD=12-26/EMA=9-21-50/BB=20 全部是股市日线标准
    # 加密5m上改为：RSI=7(35min) ATR=20(100min≈1.7h) MACD=8-21 EMA=8-13-21 BB=40(3.3h)
    _RSI_PERIOD = 7
    _ATR_PERIOD = 20
    _MACD_FAST = 8
    _MACD_SLOW = 21
    _MACD_SIGNAL = 5
    _MOMENTUM_PERIOD = 6
    _ADX_PERIOD = 10
    _BB_PERIOD = 40
    _ZSCORE_PERIOD = 40

    def compute_rsi(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """RSI指标（加密5m周期=7，原14=股市日线）"""
        close = klines['close'].values
        p = self._RSI_PERIOD
        if len(close) < p:
            return float('nan')
        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.mean(gain[-p:])
        avg_loss = np.mean(loss[-p:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return float(np.clip(rsi, 0, 100))

    def compute_macd(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """MACD值（加密5m：8/21/5，原12/26/9=股市日线）"""
        close = klines['close'].values
        if len(close) < self._MACD_SLOW:
            return float('nan')
        ema_fast = self._ema(close, self._MACD_FAST)
        ema_slow = self._ema(close, self._MACD_SLOW)
        macd_line = ema_fast[-1] - ema_slow[-1]
        signal = self._ema(np.concatenate([[0], macd_line if isinstance(macd_line, np.ndarray) else [macd_line]]), self._MACD_SIGNAL)[-1]
        return float(macd_line - signal)

    def compute_momentum(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """动量（加密5m周期=6，原10=股市日线）"""
        close = klines['close'].values
        p = self._MOMENTUM_PERIOD
        if len(close) < p:
            return 0.0
        return float((close[-1] - close[-p]) / close[-p] * 100)

    def compute_roc(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """变化率"""
        close = klines['close'].values
        p = self._MOMENTUM_PERIOD
        if len(close) < p:
            return 0.0
        return float((close[-1] - close[-p]) / close[-p] * 100)

    def compute_adx(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """ADX指标（加密5m周期=10，原14=股市日线）"""
        high = klines['high'].values
        low = klines['low'].values
        close = klines['close'].values
        p = self._ADX_PERIOD
        n = len(close)
        if n < p * 2:
            return 20.0
        plus_di  = self._calculate_directional_indicator(high, low, close, p, True)
        minus_di = self._calculate_directional_indicator(high, low, close, p, False)
        denom = plus_di + minus_di
        valid = denom > 1e-8
        with np.errstate(invalid="ignore", divide="ignore"):
            dx = np.where(valid, np.abs(plus_di - minus_di) / np.where(valid, denom, 1.0) * 100, 0.0)
        adx_val = float(np.mean(dx[-p:]))
        return adx_val if not np.isnan(adx_val) else 20.0

    # ========== 均值回归因子 ==========

    def compute_bb_width(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """布林带宽度（加密5m周期=40，原20=股市月线；5m×40≈3.3h覆盖半天）"""
        close = klines['close'].values
        p = self._BB_PERIOD
        if len(close) < p:
            return 0.05
        sma = np.mean(close[-p:])
        std = np.std(close[-p:])
        upper = sma + 2 * std
        lower = sma - 2 * std
        if sma == 0:
            return 0.05
        return float((upper - lower) / sma)

    def compute_zscore(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """价格Z分数（加密5m周期=40，原20=股市月线）"""
        close = klines['close'].values
        p = self._ZSCORE_PERIOD
        if len(close) < p:
            return 0.0
        mean = np.mean(close[-p:])
        std = np.std(close[-p:])
        if std == 0:
            return 0.0
        return float((close[-1] - mean) / std)

    def compute_atr_ratio(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """ATR/价格比"""
        atr = self.compute_atr(klines, market_data)
        close = klines['close'].values[-1]
        if close == 0:
            return 0.0
        return float(atr / close)

    # ========== 波动率因子 ==========

    def compute_atr(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """ATR指标（加密5m周期=20，原14=70min；5m×20=100min≈1.7h覆盖日内节律）"""
        high = klines['high'].values
        low = klines['low'].values
        close = klines['close'].values
        p = self._ATR_PERIOD
        if len(close) < p:
            return 0.0
        tr = np.maximum(
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
        return float(np.mean(tr[-p:]))
    
    def compute_hv(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """历史波动率 (年化)"""
        close = klines['close'].values
        if len(close) < 20:
            return 0.0
            
        returns = np.diff(np.log(close))
        vol = np.std(returns[-20:]) * np.sqrt(365 * 24)
        
        return float(vol * 100)
    
    def compute_parkinson_vol(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """Parkinson波动率"""
        high = klines['high'].values
        low = klines['low'].values
        
        if len(high) < 10:
            return 0.0

        # 防止 low=0 导致除零
        safe_low = np.where(low > 0, low, 1e-8)
        log_hl = np.log(high / safe_low)
        parkinson = np.sqrt((1 / (4 * np.log(2))) * np.mean(log_hl[-10:] ** 2))
        
        return float(parkinson * 100)
    
    # ========== 成交量因子 ==========
    
    def compute_obv(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """OBV变化率"""
        close = klines['close'].values
        volume = klines['volume'].values if 'volume' in klines.columns else np.zeros(len(close))
        
        if len(close) < 2:
            return 0.0
            
        obv = 0
        for i in range(1, len(close)):
            if close[i] > close[i-1]:
                obv += volume[i]
            elif close[i] < close[i-1]:
                obv -= volume[i]
                
        return float(obv / (volume[-1] + 1e-8))
    
    def compute_vwap(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """VWAP偏离度"""
        high = klines['high'].values
        low = klines['low'].values
        close = klines['close'].values
        volume = klines['volume'].values if 'volume' in klines.columns else np.ones(len(close))
        
        typical_price = (high + low + close) / 3
        vol_sum = np.sum(volume)
        vwap = np.sum(typical_price * volume) / (vol_sum + 1e-8)

        if close[-1] == 0 or abs(vwap) < 1e-10:
            return 0.0

        return float((close[-1] - vwap) / vwap)
    
    def compute_volume_zscore(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """成交量Z分数"""
        volume = klines['volume'].values if 'volume' in klines.columns else np.zeros(len(klines))
        
        if len(volume) < 20:
            return 0.0
            
        mean_vol = np.mean(volume[-20:])
        std_vol = np.std(volume[-20:])
        
        if std_vol == 0:
            return 0.0
            
        return float((volume[-1] - mean_vol) / std_vol)
    
    def compute_cvd_ratio(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """CVD比率"""
        if market_data and 'cvd' in market_data:
            cvd = market_data['cvd']
            total_notional = market_data.get('total_notional', 1)
            if total_notional:
                return float(cvd / total_notional)
        return 0.0
    
    # ========== 趋势因子 ==========
    
    def compute_sma_cross(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """SMA金叉/死叉信号"""
        close = klines['close'].values
        if len(close) < 50:
            return 0.0
            
        sma20 = np.mean(close[-20:])
        sma50 = np.mean(close[-50:])
        
        if sma50 == 0:
            return 0.0
            
        return float((sma20 - sma50) / sma50)
    
    def compute_ema_trend(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """EMA趋势对齐（加密5m：8/13/21，原9/21/50=股市日线）"""
        close = klines['close'].values
        if len(close) < 21:
            return 0.0

        ema_fast = self._ema(close, 8)[-1]
        ema_mid = self._ema(close, 13)[-1]
        ema_slow = self._ema(close, 21)[-1]

        # 价格在所有EMA之上 = 强趋势
        score = 0
        if close[-1] > ema_fast: score += 0.3
        if close[-1] > ema_mid: score += 0.3
        if close[-1] > ema_slow: score += 0.4
        
        return float(score)
    
    def compute_supertrend(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """SuperTrend信号"""
        close = klines['close'].values
        high = klines['high'].values
        low = klines['low'].values
        
        if len(close) < 20:
            return 0.0
            
        atr = self.compute_atr(klines, market_data)
        
        # 简化的SuperTrend计算
        upper_band = (high[-1] + low[-1]) / 2 + 3 * atr
        lower_band = (high[-1] + low[-1]) / 2 - 3 * atr
        
        if close[-1] > upper_band:
            return 1.0  # 多头
        elif close[-1] < lower_band:
            return -1.0  # 空头
        else:
            return 0.0  # 中性
    
    # ========== 市场流向因子 ==========
    
    def compute_taker_ratio(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """Taker比率 (ln(买/卖))"""
        if market_data and 'buy_notional' in market_data and 'sell_notional' in market_data:
            buy = market_data['buy_notional']
            sell = market_data['sell_notional']
            if sell > 0:
                return float(np.log(buy / sell))
        return 0.0
    
    def compute_oi_delta(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """OI变化百分比"""
        if market_data and 'oi' in market_data and 'prev_oi' in market_data:
            oi = market_data['oi']
            prev_oi = market_data['prev_oi']
            if prev_oi:
                return float((oi - prev_oi) / prev_oi * 100)
        return 0.0
    
    def compute_funding_rate(self, klines: pd.DataFrame, market_data: Any = None) -> float:
        """资金费率"""
        if market_data and 'funding_rate' in market_data:
            return float(market_data['funding_rate'] * 100)  # 转换为百分比
        return 0.0
    
    # ========== 辅助方法 ==========
    
    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """指数移动平均"""
        if len(data) < period:
            period = len(data)
        alpha = 2 / (period + 1)
        ema = np.zeros(len(data))
        ema[period-1] = np.mean(data[:period])
        for i in range(period, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
        return ema
    
    def _calculate_directional_indicator(
        self, 
        high: np.ndarray, 
        low: np.ndarray, 
        close: np.ndarray, 
        period: int,
        is_plus: bool
    ) -> np.ndarray:
        """计算+DI或-DI，返回与 high 等长的数组"""
        n = len(high)
        if n < period + 1:
            return np.zeros(n)

        high_diff = np.diff(high)   # 长度 n-1
        low_diff = np.diff(low)     # 长度 n-1

        if is_plus:
            dm = np.where(
                (high_diff > 0) & (high_diff > -low_diff),
                high_diff,
                0.0
            )
        else:
            dm = np.where(
                (low_diff > 0) & (low_diff > high_diff),
                low_diff,
                0.0
            )

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )

        # 平滑 DM 和 TR（Wilder 平滑法）
        smoothed_dm = np.zeros(n - 1)
        smoothed_tr = np.zeros(n - 1)
        smoothed_dm[period - 1] = np.sum(dm[:period])
        smoothed_tr[period - 1] = np.sum(tr[:period])
        for i in range(period, n - 1):
            smoothed_dm[i] = smoothed_dm[i-1] - smoothed_dm[i-1] / period + dm[i]
            smoothed_tr[i] = smoothed_tr[i-1] - smoothed_tr[i-1] / period + tr[i]

        di = np.zeros(n)
        valid = smoothed_tr[period-1:] > 1e-8
        di[period:][valid] = smoothed_dm[period-1:][valid] / smoothed_tr[period-1:][valid] * 100
        return di
    
    def get_factors_by_category(self, category: FactorCategory) -> List[str]:
        """获取某类别的所有因子"""
        return [name for name, config in self.FACTORS.items() 
                if config['category'] == category]
    
    def get_factor_info(self, name: str) -> Optional[Dict]:
        """获取因子信息"""
        return self.FACTORS.get(name)


# 全局实例
factor_engine = FactorEngine()
