"""策略协调器 - Strategy Coordinator

核心职责：串联所有已有组件到统一执行路径
解决问题：当前 AIStrategyEngine 跳过了 LongTermPlanner、DynamicStopManager、
          MarketAdaptor、RiskAllocator 等已实现但未接入的组件

执行流程:
1. 市场环境分析（MarketAdaptor + LongTermPlanner）
2. 长短周期策略协同（LongTermPlanner 约束 ShortTermTactician）
3. 动态风险参数计算（RiskAllocator + DynamicStopManager）
4. AI决策（注入完整上下文）
5. 动态止盈止损应用
6. 策略记忆深度更新
"""
import logging
import json
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

from sqlalchemy.orm import Session

# 2026-07-06 整改：三周期→K线周期定义统一从此处读取，不再自行硬编码
# 15m/1h/4h 等字符串，避免与 trend_classifier/multi_timeframe_orchestrator/
# signal_pre_screener 各自维护的周期定义互相矛盾（审查报告 3.7 节）。
from backend.config.tier_timeframe_map import TIER_TIMEFRAME_MAP, NATURE_TO_TIER

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class MarketEnvironment:
    """市场环境分析结果"""
    # 宏观
    market_cycle: str = "unknown"          # bull / bear / sideways / transition
    cycle_confidence: float = 0.5
    risk_budget_pct: float = 0.5           # 宏观风险预算 0~1
    
    # 微观
    volatility_regime: str = "normal"      # low / normal / high / extreme
    volatility_value: float = 0.0
    trend_direction: str = "neutral"       # bullish / bearish / neutral
    trend_strength: float = 0.0
    liquidity_score: float = 1.0           # 0~1
    
    # 适配后参数
    adapted_sl_multiplier: float = 1.0     # 止损ATR倍数调整
    adapted_tp_multiplier: float = 1.0     # 止盈ATR倍数调整
    adapted_position_scale: float = 1.0    # 仓位缩放
    adapted_entry_threshold: float = 0.6   # 入场置信度门槛
    
    # 情报融合
    sentiment_index: float = 50.0          # 综合情绪指数 0~100
    sentiment_zone: str = "neutral"        # extreme_fear / fear / neutral / greed / extreme_greed
    news_impact: float = 0.0              # 新闻对市场的影响 -1~1
    news_top_event: str = ""               # 最重大的新闻摘要
    whale_direction: float = 0.0           # 鲸鱼资金方向 -1~1
    derivatives_signal: str = "neutral"    # 衍生品综合信号
    funding_rate: float = 0.0             # 实时资金费率
    fear_greed: float = 50.0              # 恐惧贪婪指数
    
    # 数据溯源
    data_source: str = "default"           # market_adaptor / kline_analysis / market_data_analyzer / default
    price_source: str = "unknown"          # realtime / kline_fresh / kline_stale
    kline_count: int = 0                   # 分析使用的K线数量
    kline_age_hours: float = 0.0           # K线数据年龄（小时）
    current_price: float = 0.0             # 当前价格
    atr_value: float = 0.0                 # ATR 值（短周期，1h/15m 来源）
    atr_1d_value: float = 0.0              # D10: 1d ATR 绝对值（long tier 用）
    atr_1d_pct: float = 0.0                # D10: 1d ATR / price
    analysis_time: str = ""                # 分析时间
    price_stale_warning: str = ""          # 价格过期警告

    # D7: 因子引擎字段
    factor_direction: float = 0.0          # 因子合成方向 [-1, +1]
    factor_strength: float = 0.0           # 因子信号强度 [0, 1]
    factor_confidence: float = 0.0         # 因子方向一致性 [0, 1]
    factor_regime: str = "unknown"         # 因子判定市场状态
    factor_regime_confidence: float = 0.0  # 市场状态置信度

    # P0: 高阶K线特征 (12个衍生特征)
    body_ratio: float = 0.0               # K线实体占比 [0, 1]
    upper_shadow_ratio: float = 0.0        # 上影线占比
    lower_shadow_ratio: float = 0.0        # 下影线占比
    doji_score: float = 0.0               # 十字星得分 (>0.9=十字星)
    volume_price_corr: float = 0.0         # 20周期量价相关性 [-1, 1]
    volatility_skew: float = 0.0           # 波动偏度 [-1, 1]
    trend_efficiency: float = 0.0          # 趋势效率 [0, 1]
    volume_climax: float = 1.0            # 放量倍率
    price_acceleration: float = 0.0        # 价格加速度
    ema_ribbon_width: float = 0.0          # EMA带宽度
    rsi_divergence: float = 0.0            # RSI背离信号 (1.0=背离, 0.0=正常)
    volume_imbalance: float = 0.0          # 买卖失衡度 [-1, 1]

    # P0: VPVR v2 字段
    poc_price: float = 0.0                # Point of Control
    vah_price: float = 0.0                # Value Area High
    val_price: float = 0.0                # Value Area Low
    current_in_va: bool = False            # 当前价是否在价值区内
    nearest_hvn: float = 0.0              # 最近高成交量节点
    nearest_lvn: float = 0.0              # 最近低成交量节点

    # P0: 因子融合信号（三模式）
    fusion_mode: str = "ic_weighted"       # ic_weighted / weighted_vote / gated_network
    fusion_direction: float = 0.0          # 融合方向 [-1, +1]
    fusion_strength: float = 0.0           # 融合强度 [0, 1]
    fusion_confidence: float = 0.0         # 融合置信度 [0, 1]

    # P0: 多频率约束
    freq_4h_direction: int = 0             # 4h周期方向 -1/0/1
    freq_1h_direction: int = 0             # 1h周期方向
    freq_15m_direction: int = 0            # 15m周期方向
    constraint_violated: bool = False      # 是否违反硬约束
    constraint_reason: str = ""            # 违反原因

    # P2: 多频率独立分析 (15m/1h/4h 并行计算)
    m15_trend_dir: str = "neutral"          # 15m趋势方向 bullish/bearish/neutral
    m15_trend_strength: float = 0.0         # 15m趋势强度 [0, 1]
    m15_volatility_pct: float = 0.0         # 15m波动率(ATR/price)
    m15_ema20: float = 0.0                  # 15m EMA20
    m15_ema50: float = 0.0                  # 15m EMA50
    m15_rsi: float = 50.0                   # 15m RSI(14)

    m1h_trend_dir: str = "neutral"          # 1h趋势方向
    m1h_trend_strength: float = 0.0
    m1h_volatility_pct: float = 0.0
    m1h_ema20: float = 0.0
    m1h_ema50: float = 0.0
    m1h_rsi: float = 50.0

    m4h_trend_dir: str = "neutral"          # 4h趋势方向
    m4h_trend_strength: float = 0.0
    m4h_volatility_pct: float = 0.0
    m4h_ema20: float = 0.0
    m4h_ema50: float = 0.0
    m4h_rsi: float = 50.0

    multi_freq_alignment: str = "unknown"    # aligned(全部同向) / divergent(部分偏离) / conflicting(对立冲突) / unknown
    multi_freq_dominant: str = "unknown"     # 主导周期 15m/1h/4h/unknown

    # P2: 多频率对齐服务评分 (multi_freq_alignment.py)
    # 2026-07-06 整改：原名 alignment_score 与 mid_long_quant_brief.py 的
    # QuantBrief.alignment_score（0-15 整数评分，含义完全不同）同名不同义，
    # 曾导致下游误读。改名加 coordinator_ 前缀以示区分（QuantBrief 侧同步改为
    # quantbrief_alignment_score，见整改设计文档 1.5 节）。
    coordinator_alignment_score: float = 0.0  # 对齐度综合评分 [0, 1]
    entry_timing_score: float = 0.0          # 入场时机评分 [0, 1]
    recommended_leverage_scale: float = 1.0  # 推荐杠杆缩放系数
    recommended_position_scale: float = 1.0  # 推荐仓位缩放系数


@dataclass
class DynamicRiskParams:
    """动态风险参数（每次交易前实时计算）"""
    # 止损
    stop_loss_type: str = "atr_based"      # fixed_pct / atr_based / volatility_based
    stop_loss_pct: float = 0.05            # 止损百分比（固定模式）
    stop_loss_atr_multiple: float = 2.0    # ATR倍数（ATR模式）
    stop_loss_price: float = 0.0           # 计算后的止损价
    
    # 止盈（分批）
    take_profit_enabled: bool = True
    tp_levels: List[Dict[str, float]] = field(default_factory=lambda: [
        {"pct": 0.02, "close_ratio": 0.3},   # 盈利2%平30%
        {"pct": 0.04, "close_ratio": 0.3},   # 盈利4%再平30%
        {"pct": 0.08, "close_ratio": 0.4},   # 盈利8%平剩余
    ])
    
    # 移动止损
    trailing_stop_enabled: bool = True
    trailing_activation_pct: float = 0.02   # 盈利2%后激活
    trailing_distance_pct: float = 0.01     # 追踪距离1%
    
    # 时间止损
    time_stop_enabled: bool = True
    time_stop_hours: int = 72               # 超过72小时未达目标平仓
    
    # 仓位
    position_size_pct: float = 0.2          # 仓位比例
    max_leverage: float = 20.0              # 最大杠杆
    default_leverage: float = 10.0          # 默认杠杆
    actual_leverage: float = 10.0           # 本次交易实际使用的杠杆
    leverage_mode: str = "isolated"         # cross / isolated
    
    # 杠杆安全阈值
    margin_usage_limit: float = 0.70        # 保证金使用率上限（超过则不开新仓）
    liquidation_buffer_pct: float = 0.15    # 距爆仓价格的安全缓冲
    
    # 滚仓（极端行情顺势加仓）
    snowball_enabled: bool = False
    snowball_max_adds: int = 3              # 最多追加几次
    snowball_profit_threshold: float = 0.05 # 浮盈超过多少才可追加
    snowball_add_ratio: float = 0.3         # 每次追加量 = 原仓位 × 此比例


@dataclass 
class CoordinatedDecision:
    """协调后的完整决策"""
    # 基础
    symbol: str = ""
    side: str = ""                          # buy / sell / hold
    confidence: float = 0.0
    
    # AI 原始决策
    ai_reasoning: str = ""
    ai_decisions: List[Dict[str, Any]] = field(default_factory=list)
    
    # 动态风险参数
    risk_params: DynamicRiskParams = field(default_factory=DynamicRiskParams)
    
    # 市场环境
    market_env: MarketEnvironment = field(default_factory=MarketEnvironment)
    
    # 元数据
    strategy_id: str = ""
    timestamp: str = ""
    coordinator_version: str = "1.0"


# ============================================================
# 策略协调器核心
# ============================================================

class StrategyCoordinator:
    """策略协调器 - 串联所有组件的统一入口
    
    职责:
    1. 分析市场环境（调用 MarketAdaptor + LongTermPlanner）
    2. 计算动态风险参数（调用 RiskAllocator + DynamicStopManager）
    3. 构建增强 AI 决策上下文（注入市场环境 + 风险参数 + 策略记忆）
    4. 协调长短周期策略
    5. 应用动态止盈止损到执行层
    """
    
    def __init__(self, db: Session):
        self.db = db
        self._market_adaptor = None
        self._long_term_planner = None
        self._short_term_tactician = None
        self._risk_allocator = None
        # D7: 因子结果内存缓存 (symbol→{ts,data}) TTL=120s
        self._factor_cache: Dict[str, Dict[str, Any]] = {}
        self._FACTOR_CACHE_TTL = 120
    
    # === 1. 市场环境分析 ===

    def _load_env_klines(
        self,
        symbol: str,
        now_ts: int,
        exchange: str,
        kline_data: Optional[Dict] = None,
    ):
        """加载 env 分析所需的 4 个周期 K线，返回 (15m, 1h, 4h, 1d)。

        2026-07-06 整改（P1 #12/#13 落地部分）：此前 analyze_market_environment 的
        kline_data 形参声明了却从未被使用（死参数）——调用方即便传入"时点快照"K线
        也被静默忽略，函数每次自行 _get_fresh_klines 重新拉取：既是重复 DB 压力，
        也破坏了"同一决策时点用同一份 K 线"的时点一致性（重拉可能拿到更新的 bar）。

        现改为：kline_data（{period: [kline,...]} 形态）中已提供且 ≥20 根的周期直接
        复用快照，缺失/不足的周期才回退 _get_fresh_klines 实时拉取。kline_data 为
        None 时行为与旧版完全一致（四周期全部实时拉取）。
        """
        _snap = kline_data if isinstance(kline_data, dict) else None

        def _klines_for(period: str, lookback: int) -> list:
            if _snap is not None:
                _v = _snap.get(period) or _snap.get(str(period).lower())
                if _v and len(_v) >= 20:
                    return _v
            return self._get_fresh_klines(symbol, period, lookback, now_ts, exchange)

        return (
            _klines_for(TIER_TIMEFRAME_MAP["short"]["primary"], 7),
            _klines_for(TIER_TIMEFRAME_MAP["mid"]["primary"], 30),
            _klines_for(TIER_TIMEFRAME_MAP["long"]["primary"], 90),
            _klines_for(TIER_TIMEFRAME_MAP["long"]["confirm"][0], 365),
        )

    def analyze_market_environment(
        self, 
        symbol: str, 
        account_id: int = 0,
        kline_data: Optional[Dict] = None,
    ) -> MarketEnvironment:
        """分析当前市场环境 —— 直接从数据中心读取K线，用真实数据计算
        
        数据流程（简洁可靠）：
        1. 直接查 crypto_klines 表获取多周期K线
        2. 用真实K线计算：ATR、EMA趋势、波动率、成交量变化
        3. 综合多周期判断宏观周期
        4. 计算动态风险参数

        Args:
            kline_data: 可选的"时点快照"K线，形态为 {period: [kline,...]}，
                例如 {"15m": [...], "1h": [...], "4h": [...], "1d": [...]}。
                传入时对应周期直接复用快照（保证同一决策时点用同一份K线，
                并省去重复 DB 拉取）；缺失或不足 20 根的周期回退实时拉取。
                不传（None）时行为与旧版一致：全部实时拉取。
        """
        from backend.database.models import CryptoKline
        from backend.services.exchange_config import get_active_exchange
        
        env = MarketEnvironment()
        exchange = get_active_exchange()
        # 修时区 bug：naive utcnow().timestamp() 会被当作本地时区 → now_ts 滞后 TZ offset，
        # 下游 _get_fresh_klines / kline_age 判新鲜度全部错位（CST 下 -8h）
        now_ts = int(datetime.now(timezone.utc).timestamp())
        
        # =============================================
        # 1. 获取多周期K线（数据库 → API fallback）
        # =============================================
        # 周期字符串统一从 TIER_TIMEFRAME_MAP 取（short.primary=15m/mid.primary=1h/
        # long.primary=4h/long.confirm[0]=1d），不再在本文件内单独硬编码一套定义。
        # K线加载 + 时点快照路由抽到 _load_env_klines（见该方法说明，P1 #12/#13）。
        klines_15m, klines_1h, klines_4h, klines_1d = self._load_env_klines(
            symbol, now_ts, exchange, kline_data,
        )
        
        best_klines = klines_15m or klines_1h or klines_4h or klines_1d
        
        if not best_klines or len(best_klines) < 20:
            logger.warning(f"[Coordinator] {symbol} K线不足(含API回退): "
                           f"15m={len(klines_15m)}, 1h={len(klines_1h)}, 4h={len(klines_4h)}, 1d={len(klines_1d)}")
            env.data_source = "insufficient_klines"
            env.market_cycle = "unknown"
            env.cycle_confidence = 0.0
            env.trend_direction = "neutral"
            env.trend_strength = 0.0
            # 即使 K 线不足，也尝试获取实时价格
            env.current_price = self._get_realtime_price_robust(symbol, exchange)
            return self._apply_macro_constraints(env)
        
        logger.info(f"[Coordinator] {symbol} K线数据: "
                     f"15m={len(klines_15m)}, 1h={len(klines_1h)}, 4h={len(klines_4h)}, 1d={len(klines_1d)}")
        
        # =============================================
        # D7: 因子引擎接入 — 注入量化因子分析 (带缓存)
        # =============================================
        try:
            import time as _time
            _cache_key = symbol.upper()
            _cached = self._factor_cache.get(_cache_key)
            _now_ts = _time.time()
            
            if _cached and (_now_ts - _cached["ts"]) < self._FACTOR_CACHE_TTL:
                env.factor_direction = _cached["direction"]
                env.factor_strength = _cached["strength"]
                env.factor_confidence = _cached["confidence"]
                env.factor_regime = _cached["regime"]
                env.factor_regime_confidence = _cached["regime_confidence"]
                if env.factor_regime_confidence > 0.5:
                    _map = {"breakout":"expansion","continuation":"trending","reversal":"transition","absorption":"accumulation","exhaustion":"distribution","noise":"ranging"}
                    env.market_cycle = _map.get(env.factor_regime, env.market_cycle)
                    env.cycle_confidence = max(env.cycle_confidence, env.factor_regime_confidence)
                logger.debug(f"[Coordinator] {symbol} 因子缓存命中 (age={_now_ts-_cached['ts']:.0f}s)")
            else:
                import pandas as pd
                # 2026-07-06 修正：原 `services.factor_engine...` 缺少 backend. 前缀，
                # 在部分运行环境（如非 backend/ 为 CWD 启动）下会 import 失败，
                # 且失败被下面 except 吞掉，导致因子融合字段静默恒为 0（审查 4.5 #24）。
                from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator
                from backend.services.factor_engine.factor_weighting import DynamicFactorWeighting
                _df = pd.DataFrame(klines_15m if len(klines_15m) >= 20 else best_klines)
                if not _df.empty and all(c in _df.columns for c in ('open','high','low','close','volume')):
                    # 2026-07-06 补充统一：同函数内前两个 import 已改为 backend. 前缀，
                    # 这一处此前遗漏，是同一批 #24 问题的残留（同样会在非 backend/
                    # 为 CWD 的启动方式下 import 失败）。
                    from backend.services.factor_engine.base_factors import factor_engine as _fe
                    _fv = _fe.compute_all_factors(_df)
                    if _fv:
                        _sig = FactorSignalGenerator().generate_signals(_fv)
                        _fw = DynamicFactorWeighting(factor_engine=_fe)
                        _adp = _fw.calculate_adaptive_weights(_fv, None)
                        env.factor_direction = round(_sig.direction, 3)
                        env.factor_strength = round(_sig.strength, 3)
                        env.factor_confidence = round(_sig.confidence, 3)
                        env.factor_regime = _adp.regime.value
                        env.factor_regime_confidence = round(_adp.confidence, 3)
                        self._factor_cache[_cache_key] = {
                            "ts": _now_ts, "direction": env.factor_direction,
                            "strength": env.factor_strength, "confidence": env.factor_confidence,
                            "regime": env.factor_regime, "regime_confidence": env.factor_regime_confidence,
                        }
                        if env.factor_regime_confidence > 0.5:
                            _map = {"breakout":"expansion","continuation":"trending","reversal":"transition","absorption":"accumulation","exhaustion":"distribution","noise":"ranging"}
                            env.market_cycle = _map.get(env.factor_regime, env.market_cycle)
                            env.cycle_confidence = max(env.cycle_confidence, env.factor_regime_confidence)
                    else:
                        # [2026-08-15] compute_all_factors 返回空（数据不足/全拦）时
                        # 显式告警：factor_* 字段保持默认 0 属「因子不可用」，不再静默。
                        logger.warning(
                            f"[Coordinator] {symbol} compute_all_factors 返回空，"
                            f"factor_* 字段保持默认（因子不可用）"
                        )
        except Exception as _fe_err:
            # 2026-07-06 修正：因子引擎是决策上下文的一路独立输入，导入/计算失败
            # 意味着 AI 少看了一路信号却完全无感知，之前用 debug 级别会被日常
            # 日志过滤掉——升级为 error，确保运维能在监控里看到这类静默降级。
            logger.error(f"[Coordinator] 因子引擎接入失败 {symbol}（本轮 factor_* 字段将保持默认值0）: {_fe_err}", exc_info=True)

        # =============================================
        # 2. 微观分析（用短周期：15m 或 1h）
        # =============================================
        short_klines = klines_15m if len(klines_15m) >= 50 else klines_1h
        closes = [k["close"] for k in short_klines]
        highs = [k["high"] for k in short_klines]
        lows = [k["low"] for k in short_klines]
        volumes = [k["volume"] for k in short_klines]
        
        # ── 获取实时价格（多重 fallback，绝不用过期数据）──
        # 优先级：实时API价格 > K线收盘价（仅当新鲜时）
        realtime_price = self._get_realtime_price_robust(symbol, exchange)
        
        # 检查 K 线数据新鲜度
        latest_kline_ts = short_klines[-1].get("timestamp", 0)
        kline_age_hours = (now_ts - latest_kline_ts) / 3600 if latest_kline_ts > 0 else 9999
        kline_price = closes[-1]

        # 2026-07-06 整改（审查 4.5 #23）：过期数据"有比没有强"的降级策略与
        # STRICT_DATA_GATE 联动——数据确实过期时不再假装能算出可信方向，
        # 而是在下方标记 _stale_data_blocked，稍后强制 market_cycle=unknown
        # 并通过 constraint_violated 阻止新开仓判断（复用已有字段，下游无需
        # 新增读取点）。价格本身仍用过期K线兜底展示，只是不再基于它推导市场周期。
        _stale_data_blocked = False
        if realtime_price > 0:
            current_price = realtime_price
            # 如果实时价格与K线价格差距超过 10%，说明K线数据可能过期
            if kline_price > 0 and abs(realtime_price - kline_price) / kline_price > 0.10:
                logger.warning(
                    f"[Coordinator] {symbol} 价格差异警报: "
                    f"实时=${realtime_price:,.2f}, K线=${kline_price:,.2f}, "
                    f"差异={abs(realtime_price - kline_price) / kline_price * 100:.1f}%, "
                    f"K线年龄={kline_age_hours:.1f}h"
                )
        elif kline_age_hours < 2:
            # K线数据不超过2小时，可以作为 fallback
            current_price = kline_price
            logger.info(f"[Coordinator] {symbol} 使用K线收盘价 ${kline_price:,.2f} (K线{kline_age_hours:.1f}h前)")
        else:
            # K线也过期了（>2h）且无实时价格：不再"有比没有强"地继续把它当可信数据用
            current_price = kline_price
            _stale_data_blocked = True
            logger.warning(
                f"[Coordinator] ⚠️ {symbol} 数据确认过期！"
                f"无实时价格, K线已过期 {kline_age_hours:.0f}h, "
                f"展示价格仍用K线收盘价 ${kline_price:,.2f}，但 market_cycle 将被强制置为 unknown 并阻断新开仓判断"
            )
        
        # ATR（14周期）
        atr_history = []
        for i in range(1, len(short_klines)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            atr_history.append(tr)
        atr_value = sum(atr_history[-14:]) / min(len(atr_history), 14) if atr_history else 0.0
        
        # 波动率 = ATR / 价格
        vol_pct = atr_value / current_price if current_price > 0 else 0.0
        env.volatility_value = round(vol_pct, 6)
        # 根据K线周期使用不同的波动率阈值（15m ATR天然低于1h ATR）
        is_15m = short_klines is klines_15m
        if is_15m:
            extreme_th, high_th, normal_th = 0.015, 0.009, 0.003
        else:
            extreme_th, high_th, normal_th = 0.04, 0.025, 0.008
        if vol_pct > extreme_th:
            env.volatility_regime = "extreme"
        elif vol_pct > high_th:
            env.volatility_regime = "high"
        elif vol_pct > normal_th:
            env.volatility_regime = "normal"
        else:
            env.volatility_regime = "low"
        
        # 趋势：EMA20 vs EMA50
        ema20 = self._calc_ema(closes, 20)
        ema50 = self._calc_ema(closes, min(50, len(closes)))
        
        if current_price > ema20 and ema20 > ema50:
            env.trend_direction = "bullish"
            env.trend_strength = min((current_price - ema50) / ema50 * 50, 1.0) if ema50 > 0 else 0.5
        elif current_price < ema20 and ema20 < ema50:
            env.trend_direction = "bearish"
            env.trend_strength = min((ema50 - current_price) / ema50 * 50, 1.0) if ema50 > 0 else 0.5
        else:
            env.trend_direction = "neutral"
            spread = abs(ema20 - ema50) / ema50 * 100 if ema50 > 0 else 0
            env.trend_strength = min(spread / 2, 0.4)
        
        # 成交量趋势
        if len(volumes) >= 20:
            recent_vol = sum(volumes[-10:]) / 10
            older_vol = sum(volumes[-20:-10]) / 10
            env.liquidity_score = min(recent_vol / older_vol, 2.0) if older_vol > 0 else 1.0
        
        env.current_price = round(current_price, 2)
        env.atr_value = round(atr_value, 4)
        env.kline_count = len(short_klines)
        env.kline_age_hours = round(kline_age_hours, 1)
        env.data_source = "kline_analysis"
        env.analysis_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        # ── P2 D10: 计算 1d ATR（long tier 真实尺度数据源） ──
        try:
            if klines_1d and len(klines_1d) >= 15:
                _d_highs = [k["high"] for k in klines_1d]
                _d_lows = [k["low"] for k in klines_1d]
                _d_closes = [k["close"] for k in klines_1d]
                _atr_1d_list = []
                for i in range(1, len(klines_1d)):
                    _tr = max(
                        _d_highs[i] - _d_lows[i],
                        abs(_d_highs[i] - _d_closes[i - 1]),
                        abs(_d_lows[i] - _d_closes[i - 1]),
                    )
                    _atr_1d_list.append(_tr)
                if _atr_1d_list:
                    _atr_1d = sum(_atr_1d_list[-14:]) / min(len(_atr_1d_list), 14)
                    env.atr_1d_value = round(_atr_1d, 6)
                    if current_price > 0:
                        env.atr_1d_pct = round(_atr_1d / current_price, 6)
        except Exception as _e_1d:
            logger.debug(f"[Coordinator] {symbol} 1d ATR 计算失败: {_e_1d}")

        # =============================================
        # P2: 多频率独立并行分析 (15m/1h/4h)
        # =============================================
        try:
            self._analyze_per_timeframe(klines_15m, klines_1h, klines_4h, env)
        except Exception as _mfe:
            logger.debug(f"[Coordinator] {symbol} 多频率分析跳过: {_mfe}")

        # =============================================
        # P0: 高阶衍生特征计算 (F1~F12) — 从K线直接计算
        # =============================================
        try:
            self._compute_higher_order_features(env, short_klines, klines_15m)
        except Exception as _hofe:
            logger.debug(f"[Coordinator] {symbol} 高阶特征计算跳过: {_hofe}")

        # =============================================
        # P0: VPVR v2 — 成交量分布专业版
        # =============================================
        try:
            from backend.services.unified_data_pool import compute_volume_profile_v2
            _vp_data = compute_volume_profile_v2(symbol, days=3, bucket_count=50, va_pct=0.70)
            if _vp_data and not _vp_data.get("error"):
                env.poc_price = float(_vp_data.get("poc", 0) or 0)
                env.vah_price = float(_vp_data.get("vah", 0) or 0)
                env.val_price = float(_vp_data.get("val", 0) or 0)
                env.current_in_va = bool(_vp_data.get("current_in_va", False))
                _hvns = _vp_data.get("hvn", [])
                _lvns = _vp_data.get("lvn", [])
                if _hvns and env.current_price > 0:
                    env.nearest_hvn = float(min(_hvns, key=lambda x: abs(x - env.current_price)))
                if _lvns and env.current_price > 0:
                    env.nearest_lvn = float(min(_lvns, key=lambda x: abs(x - env.current_price)))
                logger.debug(
                    f"[Coordinator] {symbol} VPVR v2: "
                    f"POC={env.poc_price:.2f}, VA=[{env.val_price:.2f}, {env.vah_price:.2f}], "
                    f"in_VA={env.current_in_va}, HVN={env.nearest_hvn:.2f}, LVN={env.nearest_lvn:.2f}"
                )
            else:
                logger.debug(f"[Coordinator] {symbol} VPVR v2 数据不足")
        except Exception as _vpe:
            logger.debug(f"[Coordinator] {symbol} VPVR v2 计算跳过: {_vpe}")

        # 标记价格来源
        if realtime_price > 0:
            env.price_source = "realtime"
        elif kline_age_hours < 2:
            env.price_source = "kline_fresh"
        else:
            env.price_source = "kline_stale"
            env.price_stale_warning = (
                f"价格数据可能过期（K线{kline_age_hours:.0f}小时前），"
                f"实时价格获取失败，显示价格仅供参考"
            )
        
        # =============================================
        # 3. 宏观分析（用长周期：4h 或 1d）
        # =============================================
        long_klines = klines_4h if len(klines_4h) >= 50 else klines_1d
        if long_klines and len(long_klines) >= 30:
            long_closes = [k["close"] for k in long_klines]
            long_ema20 = self._calc_ema(long_closes, 20)
            long_ema50 = self._calc_ema(long_closes, min(50, len(long_closes)))
            long_price = long_closes[-1]
            
            # 根据K线周期正确计算回看索引
            period_hours = 4 if long_klines is klines_4h else 24
            candles_per_day = 24 / period_hours
            idx_30d = int(min(30 * candles_per_day, len(long_closes)))
            idx_90d = int(min(90 * candles_per_day, len(long_closes)))
            
            price_30d_ago = long_closes[-idx_30d]
            change_30d = (long_price - price_30d_ago) / price_30d_ago if price_30d_ago > 0 else 0
            
            if len(long_closes) >= idx_90d:
                price_90d_ago = long_closes[-idx_90d]
                change_90d = (long_price - price_90d_ago) / price_90d_ago
            else:
                change_90d = change_30d
            
            # 综合判断市场周期
            if change_30d > 0.15 and change_90d > 0.3 and long_price > long_ema20 > long_ema50:
                env.market_cycle = "bull"
                env.cycle_confidence = min(0.6 + abs(change_30d), 0.95)
                env.risk_budget_pct = 0.7
            elif change_30d < -0.15 and change_90d < -0.2 and long_price < long_ema20 < long_ema50:
                env.market_cycle = "bear"
                env.cycle_confidence = min(0.6 + abs(change_30d), 0.95)
                env.risk_budget_pct = 0.3
            elif abs(change_30d) < 0.08 and abs(change_90d) < 0.15:
                env.market_cycle = "sideways"
                env.cycle_confidence = 0.6
                env.risk_budget_pct = 0.5
            elif (change_30d > 0 and change_90d < 0) or (change_30d < 0 and change_90d > 0):
                env.market_cycle = "transition"
                env.cycle_confidence = 0.5
                env.risk_budget_pct = 0.4
            else:
                if long_price > long_ema50:
                    env.market_cycle = "bull"
                    env.cycle_confidence = 0.55
                    env.risk_budget_pct = 0.6
                else:
                    env.market_cycle = "bear"
                    env.cycle_confidence = 0.55
                    env.risk_budget_pct = 0.35
            
            logger.info(f"[Coordinator] 宏观分析: cycle={env.market_cycle}, "
                        f"30d={change_30d*100:.1f}%, 90d={change_90d*100:.1f}%, "
                        f"confidence={env.cycle_confidence:.2f}")
        else:
            env.market_cycle = "sideways"
            env.cycle_confidence = 0.4
            env.risk_budget_pct = 0.5
        
        # =============================================
        # 3.5 融合情报系统数据（新闻/情绪/衍生品/鲸鱼）
        # =============================================
        self._inject_intelligence_data(env, symbol)
        
        # =============================================
        # 4. 动态风险参数（情报感知版）
        # =============================================
        if env.volatility_regime == "extreme":
            env.adapted_sl_multiplier = 1.5
            env.adapted_tp_multiplier = 1.8
            env.adapted_position_scale = 0.6
            env.adapted_entry_threshold = 0.50
        elif env.volatility_regime == "high":
            env.adapted_sl_multiplier = 1.3
            env.adapted_tp_multiplier = 1.5
            env.adapted_position_scale = 0.8
            env.adapted_entry_threshold = 0.40
        elif env.volatility_regime == "low":
            env.adapted_sl_multiplier = 0.8
            env.adapted_tp_multiplier = 0.7
            env.adapted_position_scale = 1.2
            env.adapted_entry_threshold = 0.25
        else:
            env.adapted_sl_multiplier = 1.0
            env.adapted_tp_multiplier = 1.0
            env.adapted_position_scale = 1.0
            env.adapted_entry_threshold = 0.30
        
        if env.market_cycle == "bear":
            env.adapted_position_scale *= 0.8
            env.adapted_entry_threshold = min(env.adapted_entry_threshold + 0.05, 0.6)
        elif env.market_cycle == "bull":
            env.adapted_position_scale *= 1.10
            env.adapted_entry_threshold = max(env.adapted_entry_threshold - 0.03, 0.20)
        
        # 资金费率风控：极端费率时限制反向开仓，减仓
        fr = env.funding_rate
        if abs(fr) > 0.001:
            # 费率极高(>0.1%/8h): 做多成本高，减仓位+提高门槛
            if fr > 0.001:
                env.adapted_position_scale *= 0.7
                env.adapted_entry_threshold = min(env.adapted_entry_threshold + 0.10, 0.70)
                logger.info(f"[Coordinator] 资金费率过高({fr:.4f})，限制做多")
            elif fr < -0.001:
                env.adapted_position_scale *= 0.7
                env.adapted_entry_threshold = min(env.adapted_entry_threshold + 0.10, 0.70)
                logger.info(f"[Coordinator] 资金费率极度负向({fr:.4f})，限制做空")
        
        # 情报驱动的额外调整
        if env.sentiment_zone == "extreme_fear":
            env.adapted_entry_threshold = min(env.adapted_entry_threshold + 0.05, 0.95)
            env.adapted_position_scale *= 0.8
        elif env.sentiment_zone == "extreme_greed":
            env.adapted_entry_threshold = min(env.adapted_entry_threshold + 0.05, 0.95)
        
        if abs(env.news_impact) > 0.5:
            env.adapted_sl_multiplier *= 1.2
            env.adapted_position_scale *= 0.8
        
        # =============================================
        # P0: 因子融合编排器 (三模式) + 多频率硬约束链
        # =============================================
        try:
            _trades_count = self._get_recent_trades_count(symbol)
            self._signal_fusion_orchestrator(env, trades_history_count=_trades_count)
        except Exception as _fe:
            logger.debug(f"[Coordinator] {symbol} 因子融合跳过: {_fe}")

        try:
            _constraint_ok = self._apply_multi_freq_constraints(env, klines_15m, klines_1h, klines_4h)
            if not _constraint_ok and not env.constraint_violated:
                # 防御性兜底：函数应已在内部设置 constraint_violated=True，
                # 这里再校验一次，避免未来该函数改动时漏设标志导致门禁读不到。
                env.constraint_violated = True
                env.constraint_reason = env.constraint_reason or "多频率约束未通过（原因未记录）"
        except Exception as _mfe:
            # 2026-07-06 修正：约束检查本身异常时，之前静默跳过=视为"未违反"，
            # 是隐性的 fail-open。约束检查失败应该让下游知道"这一路信号不可信"，
            # 而不是假装通过——升级日志级别，并明确标注约束状态未知（不是已放行）。
            logger.error(f"[Coordinator] {symbol} 多频率约束检查异常（本轮约束状态视为未知，不代表已放行）: {_mfe}", exc_info=True)

        # 2026-07-06 整改（审查 4.5 #23）：过期K线(>2h)+无实时价格 时，
        # 在 STRICT_DATA_GATE 下强制收敛为 unknown 并阻断新开仓判断，
        # 不再让"有比没有强"的降级策略悄悄把过期数据当可信数据用。
        # 放在函数末尾覆盖，确保不会被前面的宏观周期计算重新写回一个具体值。
        try:
            from backend.config.settings import STRICT_DATA_GATE
        except Exception:
            STRICT_DATA_GATE = True  # 配置读取失败时按更保守的方向处理
        if _stale_data_blocked and STRICT_DATA_GATE:
            env.market_cycle = "unknown"
            env.cycle_confidence = 0.0
            env.constraint_violated = True
            env.constraint_reason = (
                f"STRICT_DATA_GATE: K线已过期{kline_age_hours:.0f}h且无实时价格，"
                f"market_cycle 强制置为 unknown，禁止基于本次分析新开仓"
            )
            logger.warning(f"[Coordinator] {symbol} {env.constraint_reason}")

        logger.info(f"[Coordinator] {symbol} 市场环境分析完成: "
                     f"cycle={env.market_cycle}({env.cycle_confidence:.0%}), "
                     f"vol={env.volatility_regime}({env.volatility_value:.4f}), "
                     f"trend={env.trend_direction}({env.trend_strength:.2f}), "
                     f"price={current_price:.2f}, source={env.data_source}, "
                     f"fusion={env.fusion_mode}(dir={env.fusion_direction:.2f},conf={env.fusion_confidence:.2f}), "
                     f"constraint={'VIOLATED' if env.constraint_violated else 'OK'}")
        
        return self._apply_macro_constraints(env)
    
    def _get_fresh_klines(
        self, symbol: str, period: str, lookback_days: int, now_ts: int, exchange: str
    ) -> List[Dict]:
        """获取新鲜的K线数据 — 数据库优先，过期则从交易所API实时拉取

        判断"过期"的标准：
        - 15m K线：最新一条 > 1小时前
        - 1h K线：最新一条 > 4小时前
        - 4h K线：最新一条 > 1天前
        - 1d K线：最新一条 > 3天前

        本函数是通用取数工具，即使数据过期也如实返回（"有比没有强"在这一层
        本身没问题——调用方拿到的是"目前能找到的最好数据"，是否据此做交易
        决策由上层判断）。真正的"过期数据禁止开仓"闸门在 analyze_market_environment
        末尾对 STRICT_DATA_GATE 的联动判断里（审查 4.5 #23），不在这里重复实现，
        避免同一条规则在两处维护。
        """
        start_ts = now_ts - lookback_days * 86400
        
        # 先查数据库
        db_klines = self._query_klines(symbol, period, start_ts, now_ts, exchange)
        
        # 判断新鲜度
        max_age_map = {"15m": 3600, "1h": 14400, "4h": 86400, "1d": 259200}
        max_age = max_age_map.get(period, 86400)
        
        is_fresh = False
        if db_klines and len(db_klines) >= 10:
            latest_ts = db_klines[-1].get("timestamp", 0)
            age = now_ts - latest_ts
            if age <= max_age:
                is_fresh = True
        
        if is_fresh:
            return db_klines
        
        # 数据库过期或不足，直接从交易所API获取
        logger.info(
            f"[Coordinator] {symbol}/{period} 数据库K线过期或不足"
            f"(db={len(db_klines)}条), 从API实时拉取..."
        )
        api_klines = self._fetch_klines_from_api(symbol, period, exchange)
        
        if api_klines and len(api_klines) >= 10:
            logger.info(f"[Coordinator] {symbol}/{period} API获取 {len(api_klines)} 条新鲜K线")
            return api_klines
        
        # API 也失败了，返回数据库的（即使过期，有比没有强）
        if db_klines:
            logger.warning(
                f"[Coordinator] {symbol}/{period} API获取失败，使用过期数据库K线 ({len(db_klines)}条)"
            )
        return db_klines

    _api_kline_cache: Dict[str, tuple] = {}
    _API_KLINE_CACHE_TTL = 90  # 同一 symbol+period 90秒内复用缓存

    # P1 #16: 近期交易数缓存（class 级，跨 StrategyCoordinator 实例复用，
    # 因为 full_auto_trading_service 每次扫描都会 new 一个新 StrategyCoordinator，
    # 实例级缓存起不到作用）。TTL=60s：高频 tick 下避免每次都全表 count。
    _trade_count_cache: Dict[str, tuple] = {}
    _TRADE_COUNT_CACHE_TTL = 60

    @staticmethod
    def _fetch_klines_from_api(symbol: str, period: str, exchange: str) -> List[Dict]:
        """直接从交易所API获取K线数据（含短TTL缓存防429）"""
        import time as _time
        cache_key = f"{symbol}_{period}_{exchange}"
        cached = StrategyCoordinator._api_kline_cache.get(cache_key)
        if cached:
            ts, data = cached
            if _time.time() - ts < StrategyCoordinator._API_KLINE_CACHE_TTL:
                return data

        count_map = {"15m": 200, "1h": 200, "4h": 200, "1d": 365}
        count = count_map.get(period, 100)
        
        result = []
        try:
            if exchange == "binance":
                from backend.services.market_data import get_kline_data
                raw = get_kline_data(symbol, "CRYPTO", period, count)
            else:
                from backend.services.market_data import get_kline_data
                raw = get_kline_data(symbol, period=period, count=count)

            if raw:
                result = [
                    {
                        "timestamp": k.get("timestamp", 0),
                        "open": float(k.get("open", 0)),
                        "high": float(k.get("high", 0)),
                        "low": float(k.get("low", 0)),
                        "close": float(k.get("close", 0)),
                        "volume": float(k.get("volume", 0)),
                    }
                    for k in raw
                ]
        except Exception as e:
            logger.warning(f"[Coordinator] API获取K线失败 {symbol}/{period}: {e}")

        if result:
            StrategyCoordinator._api_kline_cache[cache_key] = (_time.time(), result)
        return result

    def _query_klines(
        self, symbol: str, period: str, start_ts: int, end_ts: int, exchange: str
    ) -> List[Dict]:
        """从 Market DB 查询K线（CryptoKline 是 MarketBase 模型，不能走 Core DB session）。"""
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import CryptoKline
            # M1 收口：统一 K 线查询门面（数据中心）
            from backend.services.kline_data_service import kline_service as _ks
            return _ks.query_klines(
                symbol.upper(), period,
                exchange=exchange,
                start_ts=start_ts, end_ts=end_ts,
                order="asc",
            )
        except Exception as e:
            logger.warning(f"[Coordinator] 查询K线失败 {symbol}/{period}: {e}")
            return []
    
    @staticmethod
    def _get_realtime_price(symbol: str, exchange: str) -> float:
        """从交易所获取实时价格（向后兼容入口）"""
        return StrategyCoordinator._get_realtime_price_robust(symbol, exchange)

    @staticmethod
    def _get_realtime_price_robust(symbol: str, exchange: str) -> float:
        """从交易所获取实时价格 - 多重 fallback 确保拿到真实价格

        [2026-08-15 P0-3 修复] 口径统一：
        1. data_center.get_price_with_ts 秒级权威链路（2s ticker 优先），带 5s
           新鲜度校验——决策价与成交价同源；
        2. market_data.get_last_price（内部收敛到 data_center，1m 兜底带 stale 门）；
        3. DC_ONLY 下禁止 ccxt 直连兜底（原第 4 步），失败返回 None。
        """
        methods_tried = []

        # ── 秒级权威链路（ticker，5s 新鲜度校验）──
        try:
            from backend.services.data_center import TICKER_MAX_AGE_SEC, data_center
            result = data_center.get_price_with_ts(symbol, purpose="trade")
            if result:
                price, ts = result
                if price and float(price) > 0 and (
                    time.time() - float(ts or 0)
                ) <= TICKER_MAX_AGE_SEC:
                    logger.info(
                        f"[Coordinator] {symbol} 实时价格(data_center ticker): ${float(price):,.2f}"
                    )
                    return float(price)
        except Exception:
            pass
        methods_tried.append("data_center_ticker")

        # ── 统一价格服务（秒级失败后的 1m 兜底，data_center 内部带 stale 门）──
        try:
            from backend.services.market_data import get_last_price
            price = get_last_price(symbol)
            if price and price > 0:
                logger.info(f"[Coordinator] {symbol} 实时价格(market_data 兜底): ${price:,.2f}")
                return float(price)
        except Exception:
            pass
        methods_tried.append("market_data")

        try:
            # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止临时 ccxt 直连兜底。
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                methods_tried.append("temp_ccxt(blocked_dc_only)")
                logger.warning(
                    f"[Coordinator] ⚠️ {symbol} 数据中心价格不可用且 DC_ONLY 下禁止直连兜底"
                )
                return None
            import ccxt
            temp_ex = ccxt.hyperliquid({
                'enableRateLimit': True,
                'timeout': 10000,
            })
            ticker = temp_ex.fetch_ticker(f"{symbol}/USDT:USDT")
            price = float(ticker.get("last", 0) or 0)
            if price > 0:
                logger.info(f"[Coordinator] {symbol} 实时价格(临时ccxt): ${price:,.2f}")
                return price
        except Exception:
            pass
        methods_tried.append("temp_ccxt")

        logger.warning(
            f"[Coordinator] ⚠️ {symbol} 所有实时价格源均失败: {methods_tried}"
        )
        # [2026-07-10 数据修复] 原 return 0.0 会让"无法取价"静默变成"价格=0"，
        # 配合调用方的 `or 0` 形成 0 价假数据。改返回 None，让下游能区分"有价0"
        # 与"取价失败"；调用方若用 `price or 0` 仍得 0，但 data_readiness_gate 的
        # price_ok(price>0) 检查会拦住基于 0 价的开仓决策。
        return None

    @staticmethod
    def _calc_ema(data: List[float], period: int) -> float:
        """计算指数移动平均线（EMA）的最新值"""
        if not data or period <= 0:
            return 0.0
        if len(data) < period:
            return sum(data) / len(data)
        
        multiplier = 2.0 / (period + 1)
        ema = sum(data[:period]) / period  # SMA 作为初始值
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _compute_higher_order_features(
        env: 'MarketEnvironment',
        short_klines: list,
        klines_15m: list = None,
    ) -> None:
        """P0: 从K线数据计算12个高阶衍生特征 (F1~F12)
        
        使用与 unified_data_pool._capture_indicators 相同的公式，
        直接从 strategy_coordinator 已拉取的K线数据计算，避免依赖数据池内部状态。
        """
        import numpy as np

        _klines = short_klines if short_klines and len(short_klines) >= 20 else (klines_15m or [])
        if not _klines or len(_klines) < 5:
            return

        try:
            # 提取OHLCV数组
            if isinstance(_klines[0], dict):
                _opens = np.array([float(k.get('open', 0)) for k in _klines])
                _highs = np.array([float(k.get('high', 0)) for k in _klines])
                _lows = np.array([float(k.get('low', 0)) for k in _klines])
                _closes = np.array([float(k.get('close', 0)) for k in _klines])
                _volumes = np.array([float(k.get('volume', 0)) for k in _klines])
            else:
                _opens = np.array([float(k.open) for k in _klines])
                _highs = np.array([float(k.high) for k in _klines])
                _lows = np.array([float(k.low) for k in _klines])
                _closes = np.array([float(k.close) for k in _klines])
                _volumes = np.array([float(k.volume) for k in _klines])
        except Exception:
            return

        _last = len(_closes) - 1
        _o, _c, _h, _l = _opens[_last], _closes[_last], _highs[_last], _lows[_last]
        _hl_range = _h - _l if _h != _l else 1e-8

        # F1: body_ratio
        env.body_ratio = round(abs(_c - _o) / _hl_range, 4) if _hl_range > 0 else 0.0

        # F2: upper_shadow_ratio
        env.upper_shadow_ratio = round((_h - max(_o, _c)) / _hl_range, 4) if _hl_range > 0 else 0.0

        # F3: lower_shadow_ratio
        env.lower_shadow_ratio = round((min(_o, _c) - _l) / _hl_range, 4) if _hl_range > 0 else 0.0

        # F4: doji_score
        env.doji_score = round(1.0 - env.body_ratio, 4)

        # F5: volume_price_corr — 20周期量价相关性
        n = len(_closes)
        if n >= 20:
            _v20 = _volumes[-20:]
            _c20 = _closes[-20:]
            _v_std = float(np.std(_v20))
            _c_std = float(np.std(_c20))
            if _v_std > 0 and _c_std > 0:
                env.volume_price_corr = round(float(np.corrcoef(_v20, _c20)[0, 1]), 4)

        # F6: volatility_skew — 波动偏度
        if n >= 10:
            _ups = [max(float(_highs[i]) - float(_closes[i]), 0) for i in range(n - 10, n)]
            _downs = [max(float(_closes[i]) - float(_lows[i]), 0) for i in range(n - 10, n)]
            _avg_up = float(np.mean(_ups)) if _ups else 0
            _avg_down = float(np.mean(_downs)) if _downs else 0
            env.volatility_skew = round(
                (_avg_up - _avg_down) / max(_avg_up + _avg_down, 1e-8), 4)

        # F7: trend_efficiency — 趋势效率 (净位移 / 总路径)
        if n >= 20:
            _net_move = abs(float(_closes[-1]) - float(_closes[-20]))
            _total_path = float(sum(abs(np.diff(_closes[-20:]))))
            env.trend_efficiency = round(_net_move / max(_total_path, 1e-8), 4)

        # F8: volume_climax — 放量倍率
        if n >= 21:
            _v_now = float(_volumes[-1])
            _v_sma20 = float(np.mean(_volumes[-21:-1]))
            env.volume_climax = round(_v_now / max(_v_sma20, 1e-8), 4)
        else:
            env.volume_climax = 1.0

        # F9: price_acceleration — ROC(5) - ROC(20)
        if n >= 21:
            _roc5 = (float(_closes[-1]) - float(_closes[-5])) / max(abs(float(_closes[-5])), 1e-8)
            _roc20 = (float(_closes[-1]) - float(_closes[-20])) / max(abs(float(_closes[-20])), 1e-8)
            env.price_acceleration = round(_roc5 - _roc20, 6)

        # F10: ema_ribbon_width — (EMA9 - EMA50) / close
        _ema9 = StrategyCoordinator._calc_ema(list(_closes), min(9, n))
        _ema50 = StrategyCoordinator._calc_ema(list(_closes), min(50, n))
        env.ema_ribbon_width = round((_ema9 - _ema50) / max(abs(float(_closes[-1])), 1e-8), 6)

        # F11: rsi_divergence — RSI斜率 vs 价格斜率背离
        if n >= 15:
            _c_slope = float(np.polyfit(range(10), _closes[-10:].astype(float), 1)[0]) if n >= 10 else 0
            _rsi_vals = []
            for _j in range(max(0, n - 15), n - 4):
                _seg = _closes[_j:_j + 14]
                if len(_seg) >= 14:
                    _rsi_vals.append(StrategyCoordinator._calc_rsi_static(list(_seg), 14))
            if len(_rsi_vals) >= 5:
                _rsi_slope = float(np.polyfit(range(len(_rsi_vals)), _rsi_vals, 1)[0])
                env.rsi_divergence = 1.0 if (_c_slope > 0 and _rsi_slope < 0) or (_c_slope < 0 and _rsi_slope > 0) else 0.0

        # F12: volume_imbalance — 买卖失衡度
        if n >= 10:
            _buy_vol = 0.0
            _sell_vol = 0.0
            for _i in range(max(0, n - 10), n):
                _vo = float(_opens[_i])
                _vc = float(_closes[_i])
                _vv = float(_volumes[_i])
                if _vc >= _vo:
                    _buy_vol += _vv
                else:
                    _sell_vol += _vv
            _total_v = _buy_vol + _sell_vol
            env.volume_imbalance = round((_buy_vol - _sell_vol) / max(_total_v, 1e-8), 4)

        logger.debug(
            f"[Coordinator] 高阶特征: body={env.body_ratio:.3f}, trend_eff={env.trend_efficiency:.3f}, "
            f"vol_climax={env.volume_climax:.2f}, doji={env.doji_score:.2f}, "
            f"rsi_div={env.rsi_divergence:.1f}, vol_imb={env.volume_imbalance:.3f}"
        )

    @staticmethod
    def _calc_rsi_static(closes: List[float], period: int = 14) -> float:
        """静态RSI计算（用于高阶特征）"""
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(delta if delta > 0 else 0.0)
            losses.append(abs(delta) if delta < 0 else 0.0)
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    def _get_recent_trades_count(self, symbol: str) -> int:
        """获取该symbol的近期历史交易数量（用于因子融合模式选择）

        P1 #16: 原实现每次调用都对 90 天 StrategyTrade 表做一次全表 count，
        高频 tick 下持续累积数据库压力。加 60s TTL 内存缓存后，同一 symbol
        在缓存窗口内复用上次结果，允许的滞后（60s）远小于因子融合模式切换
        所需的时间尺度，不影响决策正确性。
        """
        import time as _time
        cache_key = symbol.upper()
        cached = StrategyCoordinator._trade_count_cache.get(cache_key)
        if cached:
            ts, count = cached
            if _time.time() - ts < StrategyCoordinator._TRADE_COUNT_CACHE_TTL:
                return count
        try:
            from backend.database.models import StrategyTrade
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=90)
            count = self.db.query(StrategyTrade).filter(
                StrategyTrade.symbol == symbol.upper(),
                StrategyTrade.opened_at >= cutoff,
            ).count()
            count = count if count else 0
            StrategyCoordinator._trade_count_cache[cache_key] = (_time.time(), count)
            return count
        except Exception:
            return 0

    @staticmethod
    def _analyze_per_timeframe(
        klines_15m: list,
        klines_1h: list,
        klines_4h: list,
        env: 'MarketEnvironment',
    ) -> None:
        """P2: 多频率独立并行分析

        对15m/1h/4h三个周期分别计算:
        - EMA20/EMA50 及其交叉方向
        - ATR → 波动率百分比
        - RSI(14)
        - 趋势方向与强度

        最后计算三周期对齐状态 + 主导周期。
        """
        import numpy as np

        def _extract_ohlc(klines_raw):
            """从kline列表提取OHLC"""
            if not klines_raw or len(klines_raw) < 20:
                return None, None, None
            try:
                if isinstance(klines_raw[0], dict):
                    _c = [float(k.get('close', 0)) for k in klines_raw]
                    _h = [float(k.get('high', 0)) for k in klines_raw]
                    _l = [float(k.get('low', 0)) for k in klines_raw]
                else:
                    _c = [float(k.close) for k in klines_raw]
                    _h = [float(k.high) for k in klines_raw]
                    _l = [float(k.low) for k in klines_raw]
                return _c, _h, _l
            except Exception:
                return None, None, None

        def _analyze_single(closes, highs, lows) -> dict:
            """单周期技术指标计算"""
            result = {
                "trend_dir": "neutral",
                "trend_strength": 0.0,
                "volatility_pct": 0.0,
                "ema20": 0.0,
                "ema50": 0.0,
                "rsi": 50.0,
            }
            if not closes or len(closes) < 20:
                return result

            n = len(closes)
            price = closes[-1]

            # EMA20 / EMA50
            ema20_v = StrategyCoordinator._calc_ema(closes, 20)
            ema50_v = StrategyCoordinator._calc_ema(closes, min(50, n))
            result["ema20"] = round(ema20_v, 4)
            result["ema50"] = round(ema50_v, 4)

            # 趋势方向 + 强度
            if price > ema20_v > ema50_v:
                result["trend_dir"] = "bullish"
                result["trend_strength"] = round(
                    min((price - ema50_v) / max(ema50_v, 1e-8) * 30, 1.0), 4)
            elif price < ema20_v < ema50_v:
                result["trend_dir"] = "bearish"
                result["trend_strength"] = round(
                    min((ema50_v - price) / max(ema50_v, 1e-8) * 30, 1.0), 4)
            else:
                spread = abs(ema20_v - ema50_v) / max(ema50_v, 1e-8)
                result["trend_strength"] = round(min(spread * 10, 0.4), 4)
                if price > ema50_v:
                    result["trend_dir"] = "neutral_bullish"
                else:
                    result["trend_dir"] = "neutral_bearish"

            # ATR → 波动率
            tr_list = []
            for i in range(1, n):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
                tr_list.append(tr)
            atr = float(np.mean(tr_list[-14:])) if len(tr_list) >= 14 else float(np.mean(tr_list))
            result["volatility_pct"] = round(atr / max(price, 1e-8), 6)

            # RSI(14)
            if n >= 15:
                gains, losses = [], []
                for i in range(n - 14, n):
                    delta = closes[i] - closes[i - 1]
                    gains.append(delta if delta > 0 else 0.0)
                    losses.append(abs(delta) if delta < 0 else 0.0)
                avg_gain = float(np.mean(gains))
                avg_loss = float(np.mean(losses))
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    result["rsi"] = round(100.0 - (100.0 / (1.0 + rs)), 2)
                else:
                    result["rsi"] = 100.0

            return result

        # ── 15m 分析 ──
        r15 = None
        c15, h15, l15 = _extract_ohlc(klines_15m)
        if c15:
            r15 = _analyze_single(c15, h15, l15)
            env.m15_trend_dir = r15["trend_dir"]
            env.m15_trend_strength = r15["trend_strength"]
            env.m15_volatility_pct = r15["volatility_pct"]
            env.m15_ema20 = r15["ema20"]
            env.m15_ema50 = r15["ema50"]
            env.m15_rsi = r15["rsi"]

        # ── 1h 分析 ──
        r1h = None
        c1h, h1h, l1h = _extract_ohlc(klines_1h)
        if c1h:
            r1h = _analyze_single(c1h, h1h, l1h)
            env.m1h_trend_dir = r1h["trend_dir"]
            env.m1h_trend_strength = r1h["trend_strength"]
            env.m1h_volatility_pct = r1h["volatility_pct"]
            env.m1h_ema20 = r1h["ema20"]
            env.m1h_ema50 = r1h["ema50"]
            env.m1h_rsi = r1h["rsi"]

        # ── 4h 分析 ──
        r4h = None
        c4h, h4h, l4h = _extract_ohlc(klines_4h)
        if c4h:
            r4h = _analyze_single(c4h, h4h, l4h)
            env.m4h_trend_dir = r4h["trend_dir"]
            env.m4h_trend_strength = r4h["trend_strength"]
            env.m4h_volatility_pct = r4h["volatility_pct"]
            env.m4h_ema20 = r4h["ema20"]
            env.m4h_ema50 = r4h["ema50"]
            env.m4h_rsi = r4h["rsi"]

        # ── 对齐状态判定 ──
        # 2026-07-06 修正（审查 3 #8）：原逻辑 `any(d * dirs[0] < 0 for d in dirs[1:])`
        # 以第一个方向（可能是0）为锚点比较，例如 15m=0/1h=+1/4h=-1 时 dirs[0]=0，
        # 1*0<0 和 -1*0<0 都不成立 → 不会被判定为冲突，但 1h/4h 明显对立。
        # 不再自己维护这套简化判定，直接复用 multi_freq_alignment.validate_alignment
        # 的实现（该函数已经是"先过滤零方向、再两两比较非零方向"的正确写法），
        # 避免同一个"周期冲突"结论在两处出现两套不同（且一套有 bug）的答案。
        strengths = {}
        for freq_lbl, r in [("15m", r15), ("1h", r1h), ("4h", r4h)]:
            if r is not None:
                strengths[freq_lbl] = r["trend_strength"]

        try:
            from backend.services.multi_freq_alignment import multi_freq_alignment as _mfa
            _align_result = _mfa.compute_alignment_score_from_env(env)
            env.multi_freq_alignment = _align_result.alignment_status
            env.multi_freq_dominant = _align_result.recommended_freq
            env.coordinator_alignment_score = _align_result.alignment_score
            env.recommended_leverage_scale = _align_result.recommended_leverage_scale
            env.recommended_position_scale = _align_result.recommended_position_scale
            env.entry_timing_score = _mfa.get_entry_timing_score(env)
            logger.debug(
                f"[Coordinator] 多频率分析: "
                f"15m={env.m15_trend_dir}({env.m15_trend_strength:.2f}), "
                f"1h={env.m1h_trend_dir}({env.m1h_trend_strength:.2f}), "
                f"4h={env.m4h_trend_dir}({env.m4h_trend_strength:.2f}), "
                f"align={env.multi_freq_alignment}, dom={env.multi_freq_dominant}, "
                f"score={env.coordinator_alignment_score:.3f}, entry_timing={env.entry_timing_score:.3f}, "
                f"lev_scale={env.recommended_leverage_scale:.2f}, pos_scale={env.recommended_position_scale:.2f}"
            )
        except Exception as _mfa_err:
            # 对齐服务失败时退回一个不依赖 dirs[0] 锚点的极简判定，
            # 仅用于保底展示，不作为约束依据（约束链在 _apply_multi_freq_constraints 里）。
            _nonzero = [1 if "bullish" in (r["trend_dir"]) else (-1 if "bearish" in (r["trend_dir"]) else 0)
                        for r in (r15, r1h, r4h) if r is not None]
            _nonzero = [d for d in _nonzero if d != 0]
            if len(_nonzero) >= 2:
                if len(set(_nonzero)) == 1:
                    env.multi_freq_alignment = "aligned"
                elif any(a * b < 0 for i, a in enumerate(_nonzero) for b in _nonzero[i + 1:]):
                    env.multi_freq_alignment = "conflicting"
                else:
                    env.multi_freq_alignment = "divergent"
            if strengths:
                env.multi_freq_dominant = max(strengths, key=strengths.get)
            logger.debug(f"[Coordinator] 对齐服务跳过: {_mfa_err}")

    def _inject_intelligence_data(self, env: MarketEnvironment, symbol: str) -> None:
        """从情报系统获取实时数据并注入市场环境分析"""
        try:
            from backend.services.unified_data_pool import unified_data_pool
            summary = unified_data_pool.get_intelligence_summary(symbol)
            if not summary:
                logger.debug(f"[Coordinator] {symbol} 情报数据暂不可用，使用默认值")
                return
            
            env.sentiment_index = summary.get("sentiment_index", 50.0)
            env.sentiment_zone = summary.get("sentiment_zone", "neutral")
            env.whale_direction = summary.get("whale_direction", 0.0)
            env.derivatives_signal = summary.get("derivatives_signal", "neutral")
            env.fear_greed = summary.get("sentiment_index", 50.0)  # 与 sentiment_index 同源
            
            # 解析新闻影响
            news_summary = summary.get("news_summary", "")
            if news_summary:
                env.news_top_event = news_summary[:300]
            
            # 解析衍生品信号中的资金费率
            # [2026-08-15 消费端验收] 原实现依赖 `if "Funding" in deriv_interp` 字符串
            # 闸门 + derivatives_analytics.get_cached_snapshot（进程内缓存 miss 时
            # 返回 None，被 except: pass 吞掉）→ env.funding_rate 经常静默为 0，
            # 下游把 funding=0 当真实费率（成本计算/极费率闸门被绕过）。
            # 现改为 data_center.get_derivatives 落库直读（perp_funding，
            # 缓存无关、恒可用）；缺失时保持 0 但由上游 analyst_report_builder
            # 的 N/A 标记兜底（funding=0 视为占位哨兵）。
            try:
                from backend.services.data_center import data_center
                _deriv = data_center.get_derivatives(symbol) or {}
                _fr = _deriv.get("funding_rate")
                if _fr is not None:
                    env.funding_rate = float(_fr)
            except Exception as _e:
                logger.debug(f"[Coordinator] {symbol} funding_rate 落库读取失败: {_e}")
            
            # 用情绪修正周期判断置信度
            if env.sentiment_index < 20 and env.market_cycle == "bull":
                env.cycle_confidence *= 0.7
                logger.info(f"[Coordinator] 极度恐惧({env.sentiment_index:.0f})与牛市判断矛盾，降低置信度")
            elif env.sentiment_index > 80 and env.market_cycle == "bear":
                env.cycle_confidence *= 0.7
                logger.info(f"[Coordinator] 极度贪婪({env.sentiment_index:.0f})与熊市判断矛盾，降低置信度")
            
            # 鲸鱼异动修正
            if abs(env.whale_direction) > 0.3:
                whale_str = "买入" if env.whale_direction > 0 else "卖出"
                logger.info(f"[Coordinator] 鲸鱼信号：大资金{whale_str}倾向 ({env.whale_direction:.2f})")
            
            logger.info(
                f"[Coordinator] 情报融合完成: "
                f"sentiment={env.sentiment_index:.0f}({env.sentiment_zone}), "
                f"whale={env.whale_direction:.2f}, "
                f"deriv={env.derivatives_signal}, "
                f"funding={env.funding_rate:.4f}"
            )
        except Exception as e:
            logger.warning(f"[Coordinator] 情报数据注入失败(不影响主流程): {e}")
    
    def _apply_macro_constraints(self, env: MarketEnvironment) -> MarketEnvironment:
        """用宏观判断约束微观参数（仅补充 risk_budget / liquidity 约束，避免与 analyze 中重复调整）"""

        if env.market_cycle == "bear":
            env.risk_budget_pct = min(env.risk_budget_pct, 0.3)
        elif env.market_cycle == "bull":
            env.risk_budget_pct = min(env.risk_budget_pct, 0.7)

        if env.liquidity_score < 0.5:
            env.adapted_position_scale *= 0.7

        return env
    
    # === 2. 动态风险参数计算 ===

    def _cycle_prob_tier_lean(self, market_env: "MarketEnvironment", tier: str):
        """从 market_env 的分周期字段喂周期方向概率引擎，返回 (raw_lean, active)。

        raw_lean = P涨 − P跌（未按校准缩放，保留方向符号与幅度）；
        active   = 引擎可用且该 tier 校准质量 ≥ 阈值（不达标视为不可信，调用方应跳过）。
        仅用 rsi / ema_align / atr_pct 三个 market_env 已有字段，引擎自动忽略缺失项。
        """
        try:
            from backend.services.cycle_direction_probability import cycle_probability_engine
            from backend.config.settings import CYCLE_PROB_GATE_MIN_CALIBRATION
        except Exception:
            return 0.0, False
        # 按 tier 取对应周期字段
        if tier == "short":
            rsi, e20, e50 = market_env.m15_rsi, market_env.m15_ema20, market_env.m15_ema50
            atr_pct = (market_env.atr_value / market_env.current_price
                       if market_env.current_price > 0 else None)
        elif tier == "long":
            rsi, e20, e50 = market_env.m4h_rsi, market_env.m4h_ema20, market_env.m4h_ema50
            atr_pct = market_env.m4h_volatility_pct or (
                market_env.atr_1d_pct if market_env.atr_1d_pct > 0 else None)
        else:  # mid
            rsi, e20, e50 = market_env.m1h_rsi, market_env.m1h_ema20, market_env.m1h_ema50
            atr_pct = (market_env.atr_value / market_env.current_price
                       if market_env.current_price > 0 else None)

        price = market_env.current_price
        ema_align = None
        if e20 and e50 and price > 0:
            ema_align = 1.0 if price > e20 > e50 else (-1.0 if price < e20 < e50 else 0.0)
        feats = {"rsi": rsi if rsi else None, "ema_align": ema_align, "atr_pct": atr_pct}
        res = cycle_probability_engine.estimate(tier, feats)
        if not res.available:
            return 0.0, False
        active = float(res.calibration_quality or 0.0) >= float(CYCLE_PROB_GATE_MIN_CALIBRATION)
        return float(res.prob_up - res.prob_down), active

    def calculate_dynamic_risk_params(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        strategy_config: Dict[str, Any],
        market_env: MarketEnvironment,
        atr_value: Optional[float] = None,
    ) -> DynamicRiskParams:
        """计算动态风险参数（杠杆感知版）
        
        核心逻辑:
        1. 读取策略杠杆配置，根据市场环境动态调整实际杠杆
        2. 基于 ATR + 杠杆 计算止损（杠杆越高止损越紧）
        3. 基于波动率计算分批止盈级别
        4. 杠杆约束下的仓位计算（确保距爆仓有安全缓冲）
        5. 滚仓配置注入
        """
        params = DynamicRiskParams()

        # 2026-07-06 整改（审查 3 #25）：tier 必须由调用方显式提供，不允许本函数
        # 用"主导周期"（mf_dominant）静默反推——反推会推错（例如 15m 短期噪音
        # 恰好最强时，把本该是 swing 的仓误判成 short tier，止损上限被错误收窄到
        # 4%；或反过来长线止损被放得过宽），这类错误只会在实盘出现极端行情时
        # 才暴露，早failfast 比晚出问题成本低得多。
        _tier = strategy_config.get("tier") or strategy_config.get("timeframe_tier")
        if not _tier:
            _nature = strategy_config.get("trade_nature")
            if _nature:
                _tier = NATURE_TO_TIER.get(_nature)
        if not _tier:
            raise ValueError(
                "calculate_dynamic_risk_params: strategy_config 必须显式提供 "
                "'tier'/'timeframe_tier'（short/mid/long）或 'trade_nature' "
                "（scalp/intraday/swing/trend_follow/position）之一，"
                "禁止在本函数内部用主导周期(mf_dominant)静默反推 tier。"
            )
        if _tier not in TIER_TIMEFRAME_MAP:
            raise ValueError(
                f"calculate_dynamic_risk_params: 未知 tier={_tier!r}，"
                f"必须是 {list(TIER_TIMEFRAME_MAP.keys())} 之一"
            )

        # ── 读取策略配置 ──
        base_sl_pct = strategy_config.get("stop_loss_pct", 0.05)
        base_tp_pct = strategy_config.get("take_profit_pct", 0.10)
        base_position = strategy_config.get("max_position_size", 0.2)
        
        cfg_max_leverage = max(strategy_config.get("max_leverage", 20.0), 10.0)
        cfg_default_leverage = max(strategy_config.get("default_leverage", 10.0), 5.0)
        cfg_leverage_mode = strategy_config.get("leverage_mode", "isolated")
        
        # ── 动态杠杆决定 ──
        # 波动越大杠杆适度降低，允许 5x-20x 全范围
        actual_lev = cfg_default_leverage
        if market_env.volatility_regime == "extreme":
            actual_lev = max(5.0, cfg_default_leverage * 0.5)
        elif market_env.volatility_regime == "high":
            actual_lev = max(5.0, cfg_default_leverage * 0.7)
        elif market_env.volatility_regime == "low":
            if market_env.trend_strength > 0.6:
                actual_lev = min(cfg_max_leverage, cfg_default_leverage * 1.3)
        
        if market_env.market_cycle == "bear":
            actual_lev = max(5.0, actual_lev * 0.7)
        
        actual_lev = max(5.0, min(actual_lev, cfg_max_leverage))
        params.max_leverage = cfg_max_leverage
        params.default_leverage = cfg_default_leverage
        params.actual_leverage = float(round(actual_lev))
        params.leverage_mode = cfg_leverage_mode

        # =============================================
        # P2: 多频率对齐自适应调整
        # =============================================
        mf_align = market_env.multi_freq_alignment
        mf_dominant = market_env.multi_freq_dominant

        # 多频率对齐系数
        if mf_align == "aligned":
            # 三周期同向：高置信度，放大仓位+收紧止损
            mf_position_scale = 1.15
            mf_sl_scale = 0.85
            mf_tp_scale = 1.10
            mf_time_stop_factor = 1.3
            logger.debug(f"[Coordinator] 多频率对齐(aligned)，增强仓位+收紧止损")
        elif mf_align == "conflicting":
            # 周期冲突：高风险，缩减仓位+放宽止损+禁用滚仓
            mf_position_scale = 0.65
            mf_sl_scale = 1.35
            mf_tp_scale = 0.75
            mf_time_stop_factor = 0.6
            logger.warning(f"[Coordinator] 多频率冲突(conflicting)，缩减仓位+放宽止损")
            # 概率仲裁（校准感知，弱信号近乎无影响）：周期原始方向已冲突，
            # 若"可信校准"的方向概率引擎在本 tier 也看不出明确方向，说明确实
            # 无方向可言 → 再加深缩仓；有明确倾向则维持既有 0.65（不因弱信号放松）。
            # 校准不足(active=False)时整段跳过，保持既有行为。硬约束仍在别处兜底。
            try:
                _lean, _cp_active = self._cycle_prob_tier_lean(market_env, _tier)
                if _cp_active and abs(_lean) < 0.02:
                    mf_position_scale *= 0.9
                    logger.info(
                        "[Coordinator] 概率引擎在冲突中亦无方向(lean=%.3f)，加深缩仓×0.9", _lean,
                    )
            except Exception as _cp_err:
                logger.debug("[Coordinator] cycle_prob 仲裁跳过: %s", _cp_err)
        elif mf_align == "divergent":
            # 部分偏离：中等风险，轻微减仓
            mf_position_scale = 0.85
            mf_sl_scale = 1.10
            mf_tp_scale = 0.90
            mf_time_stop_factor = 0.85
            logger.debug(f"[Coordinator] 多频率偏离(divergent)，轻微减仓")
        else:
            mf_position_scale = 1.0
            mf_sl_scale = 1.0
            mf_tp_scale = 1.0
            mf_time_stop_factor = 1.0

        # 主导周期特化调整
        if mf_dominant == "4h":
            # 4h主导 → 长周期驱动，降低短线噪音干扰
            mf_position_scale *= 0.95
            mf_time_stop_factor *= 1.2
            # 使用4h波动率校准止损
            if market_env.m4h_volatility_pct > 0:
                mf_atr_override = market_env.m4h_volatility_pct * entry_price if entry_price > 0 else 0
            else:
                mf_atr_override = None
        elif mf_dominant == "15m":
            # 15m主导 → 短线驱动，快进快出
            mf_position_scale *= 1.05
            mf_time_stop_factor *= 0.7
            mf_sl_scale *= 0.9  # 短线止损更紧
            if market_env.m15_volatility_pct > 0:
                mf_atr_override = market_env.m15_volatility_pct * entry_price if entry_price > 0 else 0
            else:
                mf_atr_override = None
        elif mf_dominant == "1h":
            # 1h主导 → 平衡
            if market_env.m1h_volatility_pct > 0:
                mf_atr_override = market_env.m1h_volatility_pct * entry_price if entry_price > 0 else 0
            else:
                mf_atr_override = None
        else:
            mf_atr_override = None

        # ── 杠杆感知的止损计算（V5.1 对称缩放版）──
        # 核心公式：杠杆越高 → 价格波动对保证金的影响越大 → 止损必须更紧
        # 安全止损 = min(base_sl, 1 / (leverage * 2)) 确保不被强平
        # V5.1: lev_scale 对称应用于 TP 和 SL，保持盈亏比不随杠杆倒挂
        lev_scale = 1.0 / max(actual_lev ** 0.15, 1.0)  # 8x→0.732, 20x→0.638
        max_safe_sl = 1.0 / (actual_lev * 2.5) if actual_lev > 1 else 0.15

        # Tier 感知的绝对 SL 上限（防中线用 4h ATR 算出 8%+ 止损）。
        # _tier 已在函数入口处由 strategy_config 显式给出并校验过，
        # 不再在这里用 mf_dominant（主导周期）静默反推。
        _tier_sl_cap = {"short": 0.04, "mid": 0.06, "long": 0.12}
        _tier_abs_max_sl = _tier_sl_cap.get(_tier, 0.08)
        max_safe_sl = min(max_safe_sl, _tier_abs_max_sl)
        
        entry_price = entry_price or 0
        # P2: 使用主导周期ATR或默认ATR
        effective_atr = mf_atr_override if mf_atr_override and mf_atr_override > 0 else atr_value
        if effective_atr and effective_atr > 0 and entry_price > 0:
            params.stop_loss_type = "atr_leverage"
            atr_pct = effective_atr / entry_price
            adjusted_multiple = 2.0 * market_env.adapted_sl_multiplier * mf_sl_scale
            params.stop_loss_atr_multiple = adjusted_multiple
            raw_sl = atr_pct * adjusted_multiple * lev_scale
            # 杠杆安全钳位：止损不能超过安全线
            params.stop_loss_pct = min(raw_sl, max_safe_sl)
        else:
            params.stop_loss_type = "fixed_leverage"
            raw_sl = base_sl_pct * market_env.adapted_sl_multiplier * mf_sl_scale * lev_scale
            params.stop_loss_pct = min(raw_sl, max_safe_sl)
        
        if side == "buy":
            params.stop_loss_price = round(entry_price * (1 - params.stop_loss_pct), 6)
        else:
            params.stop_loss_price = round(entry_price * (1 + params.stop_loss_pct), 6)
        
        # 爆仓安全缓冲检查：确保止损价与爆仓价之间有 15%+ 的缓冲
        if actual_lev > 1:
            liquidation_dist = 1.0 / actual_lev  # 爆仓距离 = 100% / 杠杆
            if params.stop_loss_pct > liquidation_dist * (1 - params.liquidation_buffer_pct):
                params.stop_loss_pct = round(liquidation_dist * 0.7, 6)
                if side == "buy":
                    params.stop_loss_price = round(entry_price * (1 - params.stop_loss_pct), 6)
                else:
                    params.stop_loss_price = round(entry_price * (1 + params.stop_loss_pct), 6)
                logger.warning(
                    f"[Coordinator] 止损触及爆仓缓冲，收紧至 {params.stop_loss_pct:.2%} "
                    f"(leverage={actual_lev}x, liquidation_dist={liquidation_dist:.2%})"
                )
        
        # ── 分批止盈计算（V5.1 对称缩放版）──
        # V5.1: 使用与 SL 相同的 lev_scale，保持盈亏比不随杠杆变化
        vol_scale = 1.0
        if market_env.volatility_regime == "high":
            vol_scale = 1.5
        elif market_env.volatility_regime == "extreme":
            vol_scale = 2.0
        elif market_env.volatility_regime == "low":
            vol_scale = 0.7
        
        tp_base = base_tp_pct * market_env.adapted_tp_multiplier * lev_scale * mf_tp_scale
        params.tp_levels = [
            {"pct": round(tp_base * 0.4 * vol_scale, 4), "close_ratio": 0.3},
            {"pct": round(tp_base * 0.8 * vol_scale, 4), "close_ratio": 0.3},
            {"pct": round(tp_base * 1.5 * vol_scale, 4), "close_ratio": 0.4},
        ]
        
        # ── 移动止损 ──
        params.trailing_stop_enabled = True
        params.trailing_activation_pct = round(tp_base * 0.5 * vol_scale, 4)
        params.trailing_distance_pct = round(params.stop_loss_pct * 0.5, 4)
        
        # ── 时间止损（高波动缩短持仓时间，降低黑天鹅风险）──
        if market_env.volatility_regime == "extreme":
            base_time_stop = 12
        elif market_env.volatility_regime == "high":
            base_time_stop = 24
        elif market_env.volatility_regime == "low":
            base_time_stop = 72
        else:
            base_time_stop = 48
        # P2: 多频率对齐调整时间止损
        params.time_stop_hours = max(4, int(round(base_time_stop * mf_time_stop_factor)))
        
        # ── 杠杆约束下的仓位计算 ──
        # 单笔最大亏损 = position × leverage × stop_loss_pct
        # 约束：单笔最大亏损 ≤ 总资金的 max_daily_loss
        max_daily = strategy_config.get("max_daily_loss", 0.10)
        lev_constrained_position = max_daily / (actual_lev * params.stop_loss_pct) if (actual_lev * params.stop_loss_pct) > 0 else base_position
        
        raw_position = (
            base_position
            * market_env.adapted_position_scale
            * market_env.risk_budget_pct
            * mf_position_scale
        )
        # 取两者中更保守的
        params.position_size_pct = round(min(raw_position, lev_constrained_position), 4)
        params.position_size_pct = max(0.02, min(params.position_size_pct, 0.5))
        
        # ── 保证金使用率限制 ──
        params.margin_usage_limit = 0.70
        
        # ── 滚仓配置注入 ──
        params.snowball_enabled = strategy_config.get("snowball_enabled", False)
        params.snowball_max_adds = strategy_config.get("snowball_max_adds", 3)
        params.snowball_profit_threshold = strategy_config.get("snowball_profit_threshold", 0.05)
        # 极端行情下禁用滚仓，防止在剧烈波动中不断加仓导致爆仓
        if market_env.volatility_regime == "extreme" and params.snowball_enabled:
            params.snowball_enabled = False
            logger.info("[Coordinator] 极端波动，已禁用滚仓")
        # P2: 多频率冲突时禁用滚仓
        if mf_align == "conflicting" and params.snowball_enabled:
            params.snowball_enabled = False
            logger.info("[Coordinator] 多频率冲突，已禁用滚仓")

        logger.info(
            f"[Coordinator] 动态风险参数: leverage={actual_lev}x/{cfg_max_leverage}x, "
            f"SL={params.stop_loss_pct:.2%} ({params.stop_loss_type}), "
            f"TP_levels={[l['pct'] for l in params.tp_levels]}, "
            f"position={params.position_size_pct:.2%}, "
            f"mf_align={mf_align}, dominant={mf_dominant}, "
            f"trailing={'ON' if params.trailing_stop_enabled else 'OFF'}, "
            f"snowball={'ON' if params.snowball_enabled else 'OFF'}"
        )
        
        return params
    
    # === 3. 构建增强 AI 决策上下文 ===
    
    def build_enhanced_context(
        self,
        strategy: Any,
        memory: Optional[Any],
        market_env: MarketEnvironment,
        risk_params: DynamicRiskParams,
        base_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """构建注入了完整协调信息的 AI 决策上下文
        
        相比原来的 _build_enhanced_trigger_context，新增:
        - 市场环境分析（周期、波动率、趋势）
        - 动态风险参数（止损止盈级别）
        - 长短周期协同建议
        - 策略记忆深度摘要
        """
        ctx = dict(base_context or {})
        
        # 策略基础信息
        ctx["ai_strategy_id"] = getattr(strategy, "strategy_id", None)
        ctx["strategy_version"] = getattr(strategy, "prompt_version", None)
        
        # 市场环境（让AI理解当前市场状态）
        ctx["market_environment"] = {
            # 2026-07-06 整改（审查 3 #6）：constraint_violated 放在顶层而非只嵌套在
            # multi_frequency.constraint 里，方便下游门禁（unified_gate 等）用一次
            # 属性访问就能拿到"是否违反硬约束"，不需要了解市场环境 dict 的嵌套结构。
            "constraint_violated": market_env.constraint_violated,
            "constraint_reason": market_env.constraint_reason,
            "macro": {
                "market_cycle": market_env.market_cycle,
                "cycle_confidence": market_env.cycle_confidence,
                "risk_budget": market_env.risk_budget_pct,
            },
            "micro": {
                "volatility_regime": market_env.volatility_regime,
                "volatility_value": round(market_env.volatility_value, 4),
                "trend_direction": market_env.trend_direction,
                "trend_strength": round(market_env.trend_strength, 4),
                "liquidity_score": round(market_env.liquidity_score, 4),
            },
            # P2: 多频率独立分析
            "multi_frequency": {
                "15m": {
                    "trend": market_env.m15_trend_dir,
                    "strength": round(market_env.m15_trend_strength, 4),
                    "volatility_pct": round(market_env.m15_volatility_pct, 6),
                    "ema20": round(market_env.m15_ema20, 4),
                    "ema50": round(market_env.m15_ema50, 4),
                    "rsi": market_env.m15_rsi,
                },
                "1h": {
                    "trend": market_env.m1h_trend_dir,
                    "strength": round(market_env.m1h_trend_strength, 4),
                    "volatility_pct": round(market_env.m1h_volatility_pct, 6),
                    "ema20": round(market_env.m1h_ema20, 4),
                    "ema50": round(market_env.m1h_ema50, 4),
                    "rsi": market_env.m1h_rsi,
                },
                "4h": {
                    "trend": market_env.m4h_trend_dir,
                    "strength": round(market_env.m4h_trend_strength, 4),
                    "volatility_pct": round(market_env.m4h_volatility_pct, 6),
                    "ema20": round(market_env.m4h_ema20, 4),
                    "ema50": round(market_env.m4h_ema50, 4),
                    "rsi": market_env.m4h_rsi,
                },
                "alignment": market_env.multi_freq_alignment,
                "dominant": market_env.multi_freq_dominant,
                # 2026-07-06 整改：改名 coordinator_alignment_score，与 QuantBrief 的
                # 0-15 整数版 alignment_score 区分命名空间（见 MarketEnvironment 字段注释）。
                "coordinator_alignment_score": round(market_env.coordinator_alignment_score, 4),
                "entry_timing_score": round(market_env.entry_timing_score, 4),
                "recommended_leverage_scale": round(market_env.recommended_leverage_scale, 4),
                "recommended_position_scale": round(market_env.recommended_position_scale, 4),
                "constraint": {
                    "freq_4h": market_env.freq_4h_direction,
                    "freq_1h": market_env.freq_1h_direction,
                    "freq_15m": market_env.freq_15m_direction,
                    "violated": market_env.constraint_violated,
                    "reason": market_env.constraint_reason,
                },
            },
            # P0: 高阶K线衍生特征 (12个) — AI可用的微观结构信息
            "higher_order_features": {
                "body_ratio": market_env.body_ratio,
                "upper_shadow_ratio": market_env.upper_shadow_ratio,
                "lower_shadow_ratio": market_env.lower_shadow_ratio,
                "doji_score": market_env.doji_score,
                "volume_price_corr": market_env.volume_price_corr,
                "volatility_skew": market_env.volatility_skew,
                "trend_efficiency": market_env.trend_efficiency,
                "volume_climax": market_env.volume_climax,
                "price_acceleration": market_env.price_acceleration,
                "ema_ribbon_width": market_env.ema_ribbon_width,
                "rsi_divergence": market_env.rsi_divergence,
                "volume_imbalance": market_env.volume_imbalance,
            },
            # P0: VPVR v2 — 成交量分布关键位
            "vpvr": {
                "poc_price": market_env.poc_price,
                "vah_price": market_env.vah_price,
                "val_price": market_env.val_price,
                "current_in_va": market_env.current_in_va,
                "nearest_hvn": market_env.nearest_hvn,
                "nearest_lvn": market_env.nearest_lvn,
            },
            # P0: 因子融合信号
            "factor_fusion": {
                "mode": market_env.fusion_mode,
                "direction": market_env.fusion_direction,
                "strength": market_env.fusion_strength,
                "confidence": market_env.fusion_confidence,
            },
            "guidance": self._generate_market_guidance(market_env),
        }
        
        # 动态风险参数（让AI知道当前的止损止盈设置）
        ctx["dynamic_risk"] = {
            "stop_loss_pct": risk_params.stop_loss_pct,
            "stop_loss_type": risk_params.stop_loss_type,
            "tp_levels": risk_params.tp_levels,
            "trailing_stop": risk_params.trailing_stop_enabled,
            "position_size_pct": risk_params.position_size_pct,
            "entry_threshold": market_env.adapted_entry_threshold,
        }
        
        # 情报系统数据（让AI了解新闻/情绪/鲸鱼/合约等外部因素）
        ctx["intelligence"] = {
            "sentiment": {
                "index": market_env.sentiment_index,
                "zone": market_env.sentiment_zone,
                "fear_greed": market_env.fear_greed,
            },
            "news": {
                "impact": market_env.news_impact,
                "top_event": market_env.news_top_event,
            },
            "whale": {
                "direction": market_env.whale_direction,
                "interpretation": "大资金买入" if market_env.whale_direction > 0.1 else "大资金卖出" if market_env.whale_direction < -0.1 else "无明显异动",
            },
            "derivatives": {
                "signal": market_env.derivatives_signal,
                "funding_rate": market_env.funding_rate,
            },
        }
        
        # 策略记忆深度摘要
        if memory:
            ctx["strategy_memory"] = {
                "total_trades": getattr(memory, "total_trades", 0),
                "win_rate": getattr(memory, "win_rate", 0.0),
                "avg_profit": getattr(memory, "avg_profit", 0.0),
                "avg_loss": getattr(memory, "avg_loss", 0.0),
                "max_drawdown": getattr(memory, "max_drawdown", 0.0),
                "sharpe_ratio": getattr(memory, "sharpe_ratio", 0.0),
                "successful_patterns": self._safe_json_load(getattr(memory, "successful_patterns", None)),
                "failed_patterns": self._safe_json_load(getattr(memory, "failed_patterns", None)),
                "key_lessons": self._safe_json_load(getattr(memory, "key_lessons", None)),
                "performance_by_regime": self._safe_json_load(getattr(memory, "performance_by_regime", None)),
            }
        
        return ctx
    
    def _generate_market_guidance(self, env: MarketEnvironment) -> str:
        """根据市场环境数据生成具体的 AI 决策指导"""
        parts = []
        price_str = f"${env.current_price:,.0f}" if env.current_price > 0 else ""
        
        # 周期判断
        cycle_map = {
            "bear": f"当前处于熊市下行周期{f'({price_str})' if price_str else ''}，应优先防守，严格止损，降低仓位至{env.risk_budget_pct*100:.0f}%以下，偏向做空或观望。",
            "bull": f"当前处于牛市上行周期{f'({price_str})' if price_str else ''}，可适度激进（仓位≤{env.risk_budget_pct*100:.0f}%），关注回调入场机会，不追高。",
            "sideways": f"当前处于震荡周期{f'({price_str})' if price_str else ''}，适合区间交易策略，仓位控制在{env.risk_budget_pct*100:.0f}%以内，快进快出。",
            "transition": f"市场处于周期转换期{f'({price_str})' if price_str else ''}，方向不明确，建议减仓观望，等待方向确认后再操作。",
        }
        parts.append(cycle_map.get(env.market_cycle, f"市场周期待确认{f'({price_str})' if price_str else ''}，建议保守操作。"))
        
        # 波动率建议
        vol_map = {
            "extreme": f"波动率极端偏高(ATR {env.atr_value:.1f})，建议减仓50%+，止损放宽至×{env.adapted_sl_multiplier:.1f}，等待波动收敛。",
            "high": f"波动率偏高(ATR {env.atr_value:.1f})，适当减仓，止损×{env.adapted_sl_multiplier:.1f}。",
            "low": f"波动率偏低(ATR {env.atr_value:.1f})，缩小止盈目标×{env.adapted_tp_multiplier:.1f}，关注突破信号。",
        }
        if env.volatility_regime in vol_map:
            parts.append(vol_map[env.volatility_regime])
        
        # 趋势建议
        if env.trend_direction == "bullish" and env.trend_strength > 0.5:
            parts.append(f"短期趋势偏多(强度{env.trend_strength:.0%})，可顺势做多，关注支撑位。")
        elif env.trend_direction == "bearish" and env.trend_strength > 0.5:
            parts.append(f"短期趋势偏空(强度{env.trend_strength:.0%})，谨慎做多，注意止损。")
        
        # 成交量
        if env.liquidity_score > 1.5:
            parts.append("近期成交量放大，关注方向性突破。")
        elif env.liquidity_score < 0.5:
            parts.append("成交量萎缩，流动性不足，建议减仓或观望。")
        
        # ── 情报系统建议 ──
        if env.sentiment_index < 20:
            parts.append(f"⚠️ 市场极度恐惧(情绪指数{env.sentiment_index:.0f})，多数情况是超卖机会，但需确认趋势反转信号再入场。")
        elif env.sentiment_index > 80:
            parts.append(f"⚠️ 市场极度贪婪(情绪指数{env.sentiment_index:.0f})，注意回调风险，不宜追高。")
        elif env.sentiment_index < 35:
            parts.append(f"市场偏恐惧(情绪{env.sentiment_index:.0f})，谨慎操作。")
        elif env.sentiment_index > 65:
            parts.append(f"市场偏乐观(情绪{env.sentiment_index:.0f})，关注高位风险。")
        
        if env.news_top_event:
            parts.append(f"📰 重要消息: {env.news_top_event}")
        
        if abs(env.whale_direction) > 0.2:
            direction = "净买入" if env.whale_direction > 0 else "净卖出"
            parts.append(f"🐋 鲸鱼监测：大资金{direction}趋势(强度{abs(env.whale_direction):.2f})，关注跟随信号。")
        
        if env.funding_rate != 0:
            if env.funding_rate > 0.01:
                parts.append(f"资金费率偏高({env.funding_rate:.4f})，多头过热，空头有优势。")
            elif env.funding_rate < -0.01:
                parts.append(f"资金费率为负({env.funding_rate:.4f})，空头过度，多头反弹概率增大。")

        # ── P2: 多频率对齐建议 ──
        if env.multi_freq_alignment == "aligned":
            dom_freq = env.multi_freq_dominant
            dir_label = {"15m": env.m15_trend_dir, "1h": env.m1h_trend_dir, "4h": env.m4h_trend_dir}.get(dom_freq, "")
            parts.append(f"✅ 多周期共振({dom_freq}主导,{dir_label})：三周期同向，可加大仓位+收紧止损，顺势而为。")
        elif env.multi_freq_alignment == "conflicting":
            parts.append("⚠️ 多周期冲突：15m/1h/4h方向不一致，建议观望或极小仓位试探，严格止损，禁用滚仓。")
        elif env.multi_freq_alignment == "divergent":
            parts.append(f"⚡ 多周期偏离(主导:{env.multi_freq_dominant})：部分周期不一致，以主导周期为准，适当减仓。")

        # 周期方向提示
        freq_msgs = []
        if env.m15_trend_dir and env.m15_trend_dir != "neutral":
            freq_msgs.append(f"15m={env.m15_trend_dir}(强度{env.m15_trend_strength:.0%})")
        if env.m1h_trend_dir and env.m1h_trend_dir != "neutral":
            freq_msgs.append(f"1h={env.m1h_trend_dir}(强度{env.m1h_trend_strength:.0%})")
        if env.m4h_trend_dir and env.m4h_trend_dir != "neutral":
            freq_msgs.append(f"4h={env.m4h_trend_dir}(强度{env.m4h_trend_strength:.0%})")
        if freq_msgs:
            parts.append(f"📊 多周期方向: {' | '.join(freq_msgs)}")

        return " ".join(parts)
    
    def _safe_json_load(self, value) -> Any:
        """安全加载 JSON 字段"""
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value) if isinstance(value, str) else None
        except (json.JSONDecodeError, TypeError):
            return None
    
    # === 4. 策略记忆深度更新 ===
    
    def update_strategy_memory_deep(
        self,
        strategy_id: str,
        decisions: List[Dict[str, Any]],
        executed_trades: List[Dict[str, Any]],
        market_env: MarketEnvironment,
    ) -> None:
        """深度更新策略记忆
        
        相比原来只更新 total_trades，新增:
        - 基于实际交易结果计算 win_rate
        - 按市场状态分类记录表现
        - 提取成功/失败模式
        - 生成关键教训
        """
        from backend.database.models import StrategyMemory, StrategyTrade
        
        try:
            memory = (
                self.db.query(StrategyMemory)
                .filter(StrategyMemory.strategy_id == strategy_id)
                .first()
            )
            
            if not memory:
                memory = StrategyMemory(
                    strategy_id=strategy_id,
                    total_trades=0,
                    win_rate=0.0,
                    avg_profit=0.0,
                    avg_loss=0.0,
                    sharpe_ratio=0.0,
                    max_drawdown=0.0,
                )
                self.db.add(memory)
            
            # 4.1 基于历史交易重新计算统计
            all_closed_trades = (
                self.db.query(StrategyTrade)
                .filter(
                    StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.status == "closed",
                )
                .all()
            )
            
            if all_closed_trades:
                pnls = [float(t.pnl or 0) for t in all_closed_trades]
                wins = [p for p in pnls if p > 0]
                losses = [p for p in pnls if p < 0]
                
                memory.total_trades = len(all_closed_trades)
                memory.win_rate = round(len(wins) / len(pnls), 4) if pnls else 0.0
                memory.avg_profit = round(sum(wins) / len(wins), 4) if wins else 0.0
                memory.avg_loss = round(sum(losses) / len(losses), 4) if losses else 0.0
                
                # 计算最大回撤
                cumulative = 0.0
                peak = 0.0
                max_dd = 0.0
                for pnl in pnls:
                    cumulative += pnl
                    peak = max(peak, cumulative)
                    drawdown = peak - cumulative
                    max_dd = max(max_dd, drawdown)
                memory.max_drawdown = round(max_dd, 4)
                
                # 简化夏普比率
                if len(pnls) >= 5:
                    import statistics
                    mean_pnl = statistics.mean(pnls)
                    std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 1.0
                    memory.sharpe_ratio = round(mean_pnl / std_pnl * (252 ** 0.5), 4) if std_pnl > 0 else 0.0
            else:
                # 没有已关闭交易，只更新总数
                memory.total_trades += len(executed_trades)
            
            # 4.2 按市场状态记录表现
            perf_by_regime = self._safe_json_load(memory.performance_by_regime) or {}
            regime_key = f"{market_env.market_cycle}_{market_env.volatility_regime}"
            
            if regime_key not in perf_by_regime:
                perf_by_regime[regime_key] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            
            for trade in executed_trades:
                trade_pnl = float(trade.get("pnl", 0))
                perf_by_regime[regime_key]["trades"] += 1
                perf_by_regime[regime_key]["total_pnl"] += trade_pnl
                if trade_pnl > 0:
                    perf_by_regime[regime_key]["wins"] += 1
            
            memory.performance_by_regime = json.dumps(perf_by_regime)
            
            # 4.3 提取关键教训（最近5笔亏损交易的共同特征）
            recent_losses = (
                self.db.query(StrategyTrade)
                .filter(
                    StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.status == "closed",
                    StrategyTrade.pnl < 0,
                )
                .order_by(StrategyTrade.closed_at.desc())
                .limit(5)
                .all()
            )
            
            if recent_losses:
                lessons = []
                for loss in recent_losses:
                    ctx = self._safe_json_load(loss.decision_context)
                    if ctx:
                        lesson = {
                            "symbol": loss.symbol,
                            "side": loss.side,
                            "pnl_pct": float(loss.pnl_pct or 0),
                            "market_regime": ctx.get("market_environment", {}).get("macro", {}).get("market_cycle", "unknown"),
                            "entry_confidence": ctx.get("confidence", 0),
                        }
                        lessons.append(lesson)
                memory.key_lessons = json.dumps(lessons)
            
            memory.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            
            logger.info(
                f"[Coordinator] 策略记忆深度更新: {strategy_id}, "
                f"trades={memory.total_trades}, wr={memory.win_rate:.2%}, "
                f"sharpe={memory.sharpe_ratio:.2f}"
            )
            
        except Exception as e:
            logger.error(f"[Coordinator] 策略记忆更新失败: {e}", exc_info=True)
            self.db.rollback()

    # === 4.5 因子融合编排器 (M-4) ===

    def _signal_fusion_orchestrator(
        self,
        env: 'MarketEnvironment',
        trades_history_count: int = 0,
    ) -> None:
        """
        三模式因子融合编排器。

        根据历史交易样本量自动切换融合模式:
        - 样本量 < 100: IC_WEIGHTED (基于因子IC的加权融合,最稳健)
        - 100 <= 样本量 < 500: WEIGHTED_VOTE (动态投票,灵活)
        - 样本量 >= 500: GATED_NETWORK (门控网络,AI驱动,充分训练后)

        将融合结果写入 env 的 fusion_* 字段。
        """
        import numpy as np

        # --- 确定融合模式 ---
        if trades_history_count >= 500:
            mode = "gated_network"
        elif trades_history_count >= 100:
            mode = "weighted_vote"
        else:
            mode = "ic_weighted"

        env.fusion_mode = mode
        factor_dir = env.factor_direction
        factor_str = env.factor_strength
        factor_conf = env.factor_confidence

        try:
            if mode == "ic_weighted":
                # --- 模式1: IC加权 ---
                # 基于因子历史IC值调整权重
                _ic_weight = max(0.3, min(factor_conf, 0.95))
                _base_dir = factor_dir * _ic_weight

                # 引入高阶K线特征作为辅助信号
                _kline_signal = 0.0
                if env.trend_efficiency > 0.6 and env.body_ratio > 0.5:
                    _kline_signal = 0.3 * (1 if env.volume_climax > 1.2 else -1)
                if env.doji_score > 0.9:
                    _kline_signal *= 0.3  # 十字星削弱信号
                if env.rsi_divergence > 0.5:
                    _kline_signal = -_kline_signal * 1.5  # RSI背离反转信号

                _raw_dir = _base_dir + _kline_signal * (1 - _ic_weight)
                env.fusion_direction = round(max(-1.0, min(1.0, _raw_dir)), 4)
                env.fusion_strength = round(
                    factor_str * _ic_weight + abs(_kline_signal) * (1 - _ic_weight), 4)
                env.fusion_confidence = round(factor_conf * _ic_weight, 4)

            elif mode == "weighted_vote":
                # --- 模式2: 动态投票 ---
                _votes = []
                _weights = []

                # 因子引擎投票
                _votes.append(factor_dir)
                _weights.append(factor_conf * 0.4)

                # 趋势效率投票
                if env.trend_efficiency > 0.5:
                    _trend_dir = 1.0 if env.trend_direction == "bullish" else (-1.0 if env.trend_direction == "bearish" else 0.0)
                    _votes.append(_trend_dir)
                    _weights.append(env.trend_efficiency * 0.25)

                # 量价相关性投票
                if abs(env.volume_price_corr) > 0.3:
                    _vpc_dir = 1.0 if env.volume_price_corr > 0 else -1.0
                    _votes.append(_vpc_dir)
                    _weights.append(abs(env.volume_price_corr) * 0.15)

                # EMA带宽度投票
                if abs(env.ema_ribbon_width) > 0.02:
                    _ema_dir = 1.0 if env.ema_ribbon_width > 0 else -1.0
                    _votes.append(_ema_dir)
                    _weights.append(min(abs(env.ema_ribbon_width) * 5, 0.2))

                # 波动偏度投票
                if abs(env.volatility_skew) > 0.2:
                    _votes.append(env.volatility_skew)
                    _weights.append(abs(env.volatility_skew) * 0.1)

                # 买卖失衡投票
                if abs(env.volume_imbalance) > 0.2:
                    _votes.append(env.volume_imbalance)
                    _weights.append(abs(env.volume_imbalance) * 0.1)

                if _votes and sum(_weights) > 0:
                    _weighted_sum = sum(v * w for v, w in zip(_votes, _weights))
                    _total_w = sum(_weights)
                    env.fusion_direction = round(max(-1.0, min(1.0, _weighted_sum / _total_w)), 4)
                    env.fusion_strength = round(abs(env.fusion_direction), 4)
                    # 置信度 = 投票一致性
                    _agreement = sum(1 for v in _votes if (v > 0) == (env.fusion_direction > 0)) / len(_votes) if _votes else 0.5
                    env.fusion_confidence = round(_agreement * factor_conf, 4)
                else:
                    env.fusion_direction = factor_dir
                    env.fusion_strength = factor_str
                    env.fusion_confidence = factor_conf

            elif mode == "gated_network":
                # --- 模式3: 门控网络 (简化版sigmoid gate) ---
                # 利用多个因子作为门控输入，sigmoid激活决定最终方向
                import math

                def _sigmoid(x: float) -> float:
                    try:
                        return 1.0 / (1.0 + math.exp(-max(-50, min(50, x))))
                    except (OverflowError, ValueError):
                        return 0.0 if x < 0 else 1.0

                # 构建门控特征向量
                _g_features = [
                    factor_dir * factor_str,          # 因子主信号
                    env.trend_efficiency * 2 - 1,     # 趋势效率 → [-1, 1]
                    env.volume_price_corr,            # 量价相关
                    env.volume_imbalance,             # 买卖失衡
                    env.ema_ribbon_width * 10,        # EMA带缩放
                    env.volatility_skew,              # 波动偏度
                    env.price_acceleration * 5,       # 价格加速度缩放
                    (env.body_ratio - 0.5) * 2,       # 实体占比 → [-1, 1]
                ]

                # 门控权重 (固定权重，充分训练后可替换为学习的权重)
                _gate_weights = [0.30, 0.20, 0.10, 0.10, 0.10, 0.08, 0.07, 0.05]

                _gate_sum = sum(f * w for f, w in zip(_g_features, _gate_weights))
                _gate_output = _sigmoid(_gate_sum * 3)  # 缩放后过sigmoid

                # sigmoid输出映射到 [-1, +1]
                env.fusion_direction = round((_gate_output - 0.5) * 2, 4)
                env.fusion_strength = round(abs(env.fusion_direction), 4)
                # 置信度 = 距离中性点的距离
                env.fusion_confidence = round(abs(_gate_output - 0.5) * 2 * factor_conf, 4)

            logger.debug(
                f"[Coordinator] 因子融合: mode={mode}, "
                f"dir={env.fusion_direction:.3f}, str={env.fusion_strength:.3f}, "
                f"conf={env.fusion_confidence:.3f}"
            )

        except Exception as e:
            logger.warning(f"[Coordinator] 因子融合失败: {e}, 回退到因子引擎原始信号")
            env.fusion_mode = "ic_weighted"
            env.fusion_direction = factor_dir
            env.fusion_strength = factor_str
            env.fusion_confidence = factor_conf

    # === 4.6 多频率硬约束链 (M-5) ===

    def _apply_multi_freq_constraints(
        self,
        env: 'MarketEnvironment',
        klines_15m: list,
        klines_1h: list,
        klines_4h: list,
    ) -> bool:
        """
        多频率硬约束链: 4h → 1h → 15m 逐级约束。

        规则:
        - 4h看多 → 1h仅可做多/观望，禁止做空
        - 4h看空 → 1h仅可做空/观望，禁止做多
        - 1h方向必须服从4h方向
        - 15m入场必须在1h价值区内
        - 15m止损不超过4h VA边界

        Returns:
            True  = 通过约束，可以交易
            False = 违反约束，禁止交易
        """
        import numpy as np

        # --- 4h方向判定 ---
        freq_4h_dir = 0
        if klines_4h and len(klines_4h) >= 20:
            try:
                if isinstance(klines_4h[0], dict):
                    c4h = [float(k.get('close', 0)) for k in klines_4h]
                else:
                    c4h = [float(k.close) for k in klines_4h]
                # 2026-07-06 修正（审查 3 #9）：np.mean(c4h[-20:]) 是简单移动平均(SMA)，
                # 变量名却叫 ema20_4h——与 _analyze_per_timeframe 里 _calc_ema 算出的
                # 真实EMA不是同一个值，会导致 freq_4h_direction 与 m4h_trend_dir
                # 在同一根K线上给出相反结论。改为调用统一的真实EMA实现。
                ema20_4h = self._calc_ema(c4h, 20)
                ema50_4h = self._calc_ema(c4h, min(50, len(c4h))) if len(c4h) >= 50 else ema20_4h
                if ema20_4h > ema50_4h * 1.005:
                    freq_4h_dir = 1   # 看多
                elif ema20_4h < ema50_4h * 0.995:
                    freq_4h_dir = -1  # 看空
            except Exception:
                pass
        env.freq_4h_direction = freq_4h_dir

        # --- 1h方向判定 (服从4h) ---
        freq_1h_dir = 0
        if klines_1h and len(klines_1h) >= 20:
            try:
                if isinstance(klines_1h[0], dict):
                    c1h = [float(k.get('close', 0)) for k in klines_1h]
                    h1h = [float(k.get('high', 0)) for k in klines_1h]
                    l1h = [float(k.get('low', 0)) for k in klines_1h]
                else:
                    c1h = [float(k.close) for k in klines_1h]
                    h1h = [float(k.high) for k in klines_1h]
                    l1h = [float(k.low) for k in klines_1h]

                # 同上：改为真实EMA，不再用 SMA 冒充
                ema9_1h = self._calc_ema(c1h, 9)
                ema21_1h = self._calc_ema(c1h, 21)
                if ema9_1h > ema21_1h * 1.003:
                    freq_1h_dir = 1
                elif ema9_1h < ema21_1h * 0.997:
                    freq_1h_dir = -1

                # 硬约束: 1h方向不能与4h方向冲突
                if freq_4h_dir != 0 and freq_1h_dir != 0 and freq_1h_dir != freq_4h_dir:
                    env.constraint_violated = True
                    env.constraint_reason = (
                        f"1h方向({freq_1h_dir})与4h方向({freq_4h_dir})冲突"
                    )
                    env.freq_1h_direction = freq_1h_dir
                    logger.info(f"[Coordinator] 多频率约束违反: {env.constraint_reason}")
                    return False
            except Exception:
                pass
        env.freq_1h_direction = freq_1h_dir

        # --- 15m方向判定 (服从1h) ---
        freq_15m_dir = 0
        if klines_15m and len(klines_15m) >= 20:
            try:
                if isinstance(klines_15m[0], dict):
                    c15m = [float(k.get('close', 0)) for k in klines_15m]
                else:
                    c15m = [float(k.close) for k in klines_15m]

                # 同上：改为真实EMA，不再用 SMA 冒充
                ema9_15m = self._calc_ema(c15m, 9)
                ema21_15m = self._calc_ema(c15m, 21)
                if ema9_15m > ema21_15m * 1.002:
                    freq_15m_dir = 1
                elif ema9_15m < ema21_15m * 0.998:
                    freq_15m_dir = -1

                # 硬约束: 15m方向不能与1h方向冲突
                if freq_1h_dir != 0 and freq_15m_dir != 0 and freq_15m_dir != freq_1h_dir:
                    env.constraint_violated = True
                    env.constraint_reason = (
                        f"15m方向({freq_15m_dir})与1h方向({freq_1h_dir})冲突"
                    )
                    env.freq_15m_direction = freq_15m_dir
                    logger.info(f"[Coordinator] 多频率约束违反: {env.constraint_reason}")
                    return False
            except Exception:
                pass
        env.freq_15m_direction = freq_15m_dir

        # --- VPVR价值区约束 ---
        if env.current_in_va is False and env.poc_price > 0:
            # 当前价不在VA内，检查是否在向VA回归
            if env.current_price > env.vah_price and freq_1h_dir >= 0:
                env.constraint_violated = True
                env.constraint_reason = (
                    f"当前价{env.current_price}高于VAH{env.vah_price}且1h非空头"
                )
                logger.info(f"[Coordinator] VPVR约束违反: {env.constraint_reason}")
                return False
            if env.current_price < env.val_price and freq_1h_dir <= 0:
                env.constraint_violated = True
                env.constraint_reason = (
                    f"当前价{env.current_price}低于VAL{env.val_price}且1h非多头"
                )
                logger.info(f"[Coordinator] VPVR约束违反: {env.constraint_reason}")
                return False

        env.constraint_violated = False
        env.constraint_reason = ""
        logger.debug(
            f"[Coordinator] 多频率约束通过: "
            f"4h={freq_4h_dir}, 1h={freq_1h_dir}, 15m={freq_15m_dir}, "
            f"VA={'内' if env.current_in_va else '外'}"
        )
        return True

    # === 5. 决策质量评分 ===
    
    def score_decision_quality(
        self,
        decision: Dict[str, Any],
        trade_result: Optional[Dict[str, Any]] = None,
    ) -> float:
        """评估单次决策质量 (0-100)
        
        评分维度:
        - AI 置信度 (20分)
        - 市场环境一致性 (20分)
        - 风险收益比 (20分)
        - 实际结果 (40分，如果有)
        """
        score = 0.0
        
        # 维度1：AI 置信度（0-20分）
        confidence = decision.get("confidence", 0.5)
        score += min(confidence * 20, 20)
        
        # 维度2：市场环境一致性（0-20分）
        # 做多时趋势为 bullish 加分，趋势为 bearish 减分
        side = decision.get("side", "")
        trend = decision.get("market_env", {}).get("trend_direction", "neutral")
        if (side == "buy" and trend == "bullish") or (side == "sell" and trend == "bearish"):
            score += 20  # 顺势
        elif trend == "neutral":
            score += 10  # 中性
        else:
            score += 2   # 逆势
        
        # 维度3：风险收益比（0-20分）
        sl_pct = decision.get("stop_loss_pct", 0.05)
        tp_pct = decision.get("take_profit_pct", 0.10)
        if sl_pct > 0:
            rr_ratio = tp_pct / sl_pct
            if rr_ratio >= 3:
                score += 20
            elif rr_ratio >= 2:
                score += 15
            elif rr_ratio >= 1.5:
                score += 10
            else:
                score += 5
        
        # 维度4：实际结果（0-40分，需要交易关闭后评估）
        if trade_result:
            pnl_pct = trade_result.get("pnl_pct", 0)
            if pnl_pct > 0.05:
                score += 40  # 大赚
            elif pnl_pct > 0.01:
                score += 30  # 小赚
            elif pnl_pct > -0.01:
                score += 20  # 微亏（正常止损）
            elif pnl_pct > -0.03:
                score += 10  # 中亏
            else:
                score += 0   # 大亏
        
        return round(min(score, 100), 2)
