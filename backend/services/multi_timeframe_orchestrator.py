"""
多周期策略编排器 — 统一协调短/中/长期策略

核心职责:
1. 调用 LongTermPlanner 判定大方向和风险预算
2. 中期层扫描波段机会 + 匹配回测验证模板
3. 短期层精确入场时机 + 事件驱动快速反应
4. 综合三层信号输出协调后的交易指令
5. 处理新闻/鲸鱼等紧急事件的覆盖规则
"""
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════ 协调规则（旧查找表已移除，由 _coordinate 加权共识取代） ═══════════════════

# 方向 → 数值映射（用于加权共识评分）
_BIAS_SCORE: Dict[str, float] = {
    "bullish": 1.0, "neutral": 0.0, "bearish": -1.0,
}

EVENT_OVERRIDE_RULES: Dict[str, Dict[str, Any]] = {
    # 2026-06-18: 冻结时长大幅缩短。原 blackswan 30min/regulation 15min 太长，
    # 加密市场 30 分钟足以走完一轮插针反弹，冻结到那时已无意义。
    # 现：黑天鹅 10min（仅挡第一波恐慌），监管 5min（消化新闻）。
    "blackswan_negative":  {"action": "close_all_long",      "freeze_minutes": 10},
    "blackswan_positive":  {"action": "close_all_short",     "freeze_minutes": 10},
    "whale_massive_sell":  {"action": "tighten_sl_50pct",    "reduce_position": 0.5},
    "whale_massive_buy":   {"action": "hold",                "allow_add_position": True},
    "regulation_negative": {"action": "reduce_to_30pct",     "freeze_minutes": 5},
    "extreme_fear":        {"action": "contrarian_small_long","max_position": 0.1},
    "extreme_greed":       {"action": "tighten_tp",          "reduce_new_long": True},
}


@dataclass
class TimeframeView:
    """单一周期的分析结果"""
    timeframe: str           # "long" / "mid" / "short"
    bias: str = "neutral"    # "bullish" / "bearish" / "neutral"
    confidence: float = 0.0
    suggested_action: str = "wait"
    key_levels: Dict[str, float] = field(default_factory=dict)
    details: str = ""


@dataclass
class OrchestratorDecision:
    """编排器最终决策"""
    symbol: str
    timestamp: float = 0.0

    # 三层分析
    long_view: TimeframeView = field(default_factory=lambda: TimeframeView("long"))
    mid_view: TimeframeView = field(default_factory=lambda: TimeframeView("mid"))
    short_view: TimeframeView = field(default_factory=lambda: TimeframeView("short"))

    # 协调结果
    allowed_direction: str = "both"     # long_only / short_only / both / none
    position_multiplier: float = 0.5
    coordination_note: str = ""

    # 事件覆盖
    event_override: Optional[Dict] = None
    event_note: str = ""

    # 最终建议
    final_action: str = "wait"
    final_side: str = ""                # long / short / ""
    final_position_pct: float = 0.0
    final_leverage: float = 10.0
    final_sl_pct: float = 0.03
    final_tp_pct: float = 0.08
    risk_budget_pct: float = 0.5
    reasoning: str = ""

    # 情报摘要
    sentiment_index: float = 50.0
    sentiment_zone: str = "neutral"

    # 槽位推荐（独立判断各周期槽位）
    recommended_slots: List[str] = field(default_factory=list)  # ["long","mid","short"] 或 []
    slot_actions: Dict[str, str] = field(default_factory=dict)  # {"long":"create","mid":"create","short":"pause"}
    slot_reasoning: Dict[str, str] = field(default_factory=dict)  # 各槽位的激活理由

    # 推荐交易性质（基于 L/M/S 分析结果，供策略创建时分配 tier）
    recommended_nature: str = "swing"  # scalp / intraday / swing / trend_follow / position

    # P1-7: 市场状态注入 (MarketRegimeClassifier 分类结果)
    regime: str = ""                    # trending_up / trending_down / ranging / high_volatility / low_volatility / crash
    regime_confidence: float = 0.0
    position_scale: float = 1.0         # Crash→0, Ranging→0.25, TrendingUp→1.3, 默认1.0


class MultiTimeframeOrchestrator:
    """多周期策略编排器（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    # 可配参数的默认值（进化器可通过 load_params 覆盖）
    DEFAULT_PARAMS = {
        "mid_rsi_bull": 55,
        "mid_rsi_bear": 45,
        "mid_rsi_weak_bull": 52,
        "mid_rsi_weak_bear": 48,
        "mid_conf_strong": 0.85,
        "mid_conf_weak": 0.35,
        "mid_conf_neutral": 0.25,
        "intel_fusion_min_conf": 0.1,
        "intel_fusion_neutral_boost": 0.3,
        "intel_fusion_agree_boost": 0.15,
        "intel_fusion_conflict_mult": 0.5,
        "long_fgi_extreme_fear": 25,
        "long_fgi_extreme_greed": 75,
        "long_fgi_fear": 40,
        "long_fgi_greed": 60,
        "long_intel_min_conf": 15,
        "short_whale_threshold": 0.3,
        "finalize_long_weight": 0.30,    # 长期权重 30%（原 25%）
        "finalize_mid_weight": 0.40,      # 中期权重 40%（原 45%）
        "finalize_short_weight": 0.30,    # 短期权重 30%（不变）
        "finalize_min_conf": 0.1,
        "finalize_max_active_ratio": 0.6,
        "finalize_mid_fallback_conf": 0.25,  # 中期兜底门槛（原 0.15）
        "finalize_long_fallback_conf": 0.30,  # 长期兜底门槛（原 0.2）
        "short_conf_threshold": 0.30,      # 新增：短期置信度门槛（降低）
    }

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._last_decisions: Dict[str, OrchestratorDecision] = {}
        self._freeze_until: Dict[str, float] = {}
        self._freeze_reason: Dict[str, str] = {}
        self._triggered_news_hashes: set = set()
        self._params: Dict[str, Any] = dict(self.DEFAULT_PARAMS)
        # bias 稳定冷却：防止震荡市频繁翻转
        self._last_side: Dict[str, str] = {}          # symbol → "long"/"short"/""
        self._side_confirm_count: Dict[str, int] = {}  # 连续同向确认次数
        self._side_confirm_ts: Dict[str, float] = {}   # 上次 side 改变时间
        self._pending_flip: Dict[str, str] = {}         # symbol → 待确认的新方向
        self._pending_flip_ts: Dict[str, float] = {}    # symbol → 首次检测到翻转的时间
        self._intel_cache: Dict[str, Any] = {}        # symbol → 情报信号缓存（同一评估周期复用）
        self._intel_cache_ts: float = 0.0              # 缓存时间戳

        # P1 #15（审查 3）：本单例的可变状态在 evaluate_portfolio 的线程池并发场景下
        # 此前完全无锁读写——同一 symbol 若被主循环和独立循环同时评估，
        # _freeze_until/_last_side/_pending_flip 等字典的"读旧值→改→写回"复合操作
        # 不是原子的，会出现冻结状态错乱、flip 确认计数串台。
        # 用 per-symbol 锁而不是一把全局大锁：不同 symbol 之间仍可完全并行，
        # 只序列化"同一 symbol 的并发评估"这一真正有竞态风险的场景。
        self._symbol_locks: Dict[str, threading.Lock] = {}
        self._symbol_locks_guard = threading.Lock()   # 保护 _symbol_locks 字典本身的创建过程
        # _intel_cache 是跨 symbol 共享的全局缓存（非 per-symbol 键值隔离语义——
        # evaluate_portfolio 会整体 clear()），per-symbol 锁保护不了"整体清空 vs
        # 单 symbol 读写"之间的竞态，需要单独一把全局锁。
        self._intel_cache_lock = threading.Lock()
        logger.info("[MTOrchestrator] 多周期编排器初始化完成")

    def _get_symbol_lock(self, symbol: str) -> threading.Lock:
        """按 symbol 取（不存在则创建）专属锁，用于保护该 symbol 的可变状态读写段。"""
        lock = self._symbol_locks.get(symbol)
        if lock is None:
            with self._symbol_locks_guard:
                lock = self._symbol_locks.get(symbol)
                if lock is None:
                    lock = threading.Lock()
                    self._symbol_locks[symbol] = lock
        return lock

    def load_params(self, params: Dict[str, Any]):
        """加载外部参数配置（进化器优化后的参数）"""
        for k, v in params.items():
            if k in self.DEFAULT_PARAMS:
                self._params[k] = v
        logger.info(f"[MTOrchestrator] 已加载 {len(params)} 个参数覆盖")

    def get_params(self) -> Dict[str, Any]:
        """返回当前参数配置"""
        return dict(self._params)

    # ════════════════════════ 主入口 ════════════════════════

    def _get_intel_signal(self, symbol: str):
        """获取情报信号（带缓存，同一评估周期内只计算一次）。

        P1 #15：_intel_cache 是跨 symbol 共享的字典，evaluate_portfolio 会在
        批量评估开始时整体 clear() 一次——用专门的 _intel_cache_lock（而非
        per-symbol 锁）保护，避免"正在清空"和"正在读写某个 symbol"两个线程
        互相踩踏（其中一种典型后果：clear() 清完之后，另一线程仍把上一周期
        算出的旧值写回，导致新周期第一次评估复用了上一周期的情报数据）。
        """
        _now = time.time()
        with self._intel_cache_lock:
            # 缓存有效期 60s，确保同一批次的 evaluate 调用复用
            if symbol in self._intel_cache and (_now - self._intel_cache_ts) < 60:
                return self._intel_cache[symbol]
        try:
            from backend.services.intelligence_signal_engine import intelligence_signal_engine
            intel = intelligence_signal_engine.compute_trading_signal(symbol)
            with self._intel_cache_lock:
                self._intel_cache[symbol] = intel
                self._intel_cache_ts = _now
            return intel
        except Exception:
            return None

    @staticmethod
    def _extract_higher_order_features(ind: dict) -> dict:
        """从 indicators 字典安全提取高阶K线衍生特征 (F1~F12)"""
        _feat_names = [
            "body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
            "doji_score", "volume_price_corr", "volatility_skew",
            "trend_efficiency", "volume_climax", "price_acceleration",
            "ema_ribbon_width", "rsi_divergence", "volume_imbalance",
        ]
        return {k: float(ind.get(k, 0) or 0) for k in _feat_names}

    def evaluate(self, symbol: str, snapshot=None) -> OrchestratorDecision:
        """对单个交易对进行多周期评估（对外入口）。

        P1 #15（审查 3）：加 per-symbol 锁后再转发到 _evaluate_impl，
        确保同一 symbol 不会被两个线程（如主循环 + 独立调度循环）同时评估、
        并发读写 _freeze_until/_last_side/_pending_flip 等实例状态。
        不同 symbol 之间不共享锁，互相之间仍然完全并行。
        """
        with self._get_symbol_lock(symbol):
            return self._evaluate_impl(symbol, snapshot)

    def _evaluate_impl(self, symbol: str, snapshot=None) -> OrchestratorDecision:
        """
        对单个交易对进行多周期评估（实现细节，调用方应始终走 evaluate()）

        Args:
            symbol: 交易对
            snapshot: UnifiedSnapshot（可选，没传则自动获取）
        """
        decision = OrchestratorDecision(symbol=symbol, timestamp=time.time())

        # 检查冻结
        if self._is_frozen(symbol):
            remaining = self._freeze_until.get(symbol, 0) - time.time()
            reason = self._freeze_reason.get(symbol, "未知原因")
            decision.final_action = "frozen"
            decision.reasoning = f"事件冻结中(剩余{max(0, int(remaining / 60))}分钟): {reason}"
            return decision

        # 获取数据快照
        if snapshot is None:
            snapshot = self._get_snapshot(symbol)

        # 第1步: 长期分析 (1d/1w)
        decision.long_view = self._analyze_long_term(symbol, snapshot)

        # 第2步: 中期分析 (1h/4h)
        decision.mid_view = self._analyze_mid_term(symbol, snapshot)

        # 第3步: 短期分析 (5m/15m)
        decision.short_view = self._analyze_short_term(symbol, snapshot)

        # 第3.5步: 对齐三周期置信度（数据就绪时才继承，禁止假填充）
        self._align_tier_confidences(decision, snapshot=snapshot, symbol=symbol)

        # 第4步: 注入情绪/情报
        self._inject_intelligence(decision, snapshot)

        # 第4.5步(NEW): 注入市场状态分类 (P1-7)
        # MarketRegimeClassifier 的7类流分析结果传入 _coordinate()
        # Crash→禁止开仓, Ranging→仓位减半, TrendingUp→仓位×1.3
        self._inject_regime(decision, snapshot)

        # 第5步: 事件覆盖检查（先于_coordinate，确保事件驱动的bias/confidence变更被协调算法感知）
        self._check_event_overrides(decision, snapshot)

        # 第6步: 三层协调（使用事件覆盖后的视图）
        self._coordinate(decision)

        # 第7步: 输出最终建议
        self._finalize(decision, snapshot)

        # 第7.5步: 多频率硬约束链 H1-H5（2026-07-06 接入，此前定义了却从未被调用，
        # 是审查报告 3 #4 指出的死代码——约束"写了但不生效"。放在 _finalize 之后
        # 是因为约束需要修正的是 _finalize 算出的 final_position_pct/final_sl_pct/
        # final_action 等最终结论；放在 _recommend_slots 之前，是因为槽位推荐要基于
        # 约束修正后的 view/final_action，否则约束虽然生效了但槽位推荐仍用旧结论，
        # 等于白接。
        self._apply_frequency_constraints(decision, symbol=symbol)

        # 第8步(NEW): 智能槽位推荐 — 决定开几个周期、开哪些
        self._recommend_slots(decision)

        try:
            from backend.config.settings import STRICT_DATA_GATE
            if STRICT_DATA_GATE:
                from backend.services.data_readiness_gate import gate_orchestrator_decision
                decision = gate_orchestrator_decision(decision, snapshot, symbol)
        except Exception as _gate_err:
            logger.debug(f"[MTOrchestrator] data gate skip: {_gate_err}")

        self._last_decisions[symbol] = decision
        slots_str = ",".join(decision.recommended_slots) if decision.recommended_slots else "none"
        logger.info(
            f"[MTOrchestrator] {symbol}: "
            f"L={decision.long_view.bias}({decision.long_view.confidence:.0%}) "
            f"M={decision.mid_view.bias}({decision.mid_view.confidence:.0%}) "
            f"S={decision.short_view.bias}({decision.short_view.confidence:.0%}) → "
            f"slots=[{slots_str}] {decision.final_action} {decision.final_side} | "
            f"情绪{decision.sentiment_index:.0f}({decision.sentiment_zone})"
        )
        return decision

    def evaluate_portfolio(self, symbols: List[str], snapshot=None) -> Dict[str, OrchestratorDecision]:
        """批量评估多个交易对（并行加速，用线程池避免串行阻塞）。"""
        if len(symbols) <= 3:
            # 少量币种直接串行，避免线程开销
            return {s: self.evaluate(s, snapshot) for s in symbols}

        import concurrent.futures
        results: Dict[str, OrchestratorDecision] = {}
        # 清空情报缓存，确保新周期从头计算
        # P1 #15：加锁清空，避免与并发中的 _get_intel_signal 读写交叉。
        with self._intel_cache_lock:
            self._intel_cache.clear()
            self._intel_cache_ts = 0.0

        def _eval_one(sym):
            return sym, self.evaluate(sym, snapshot)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(symbols), 6),
            thread_name_prefix="orch-eval",
        ) as pool:
            futures = {pool.submit(_eval_one, s): s for s in symbols}
            for fut in concurrent.futures.as_completed(futures, timeout=90):
                try:
                    sym, dec = fut.result(timeout=10)
                    results[sym] = dec
                except Exception as _e:
                    _failed_sym = futures[fut]
                    logger.warning(f"[MTOrchestrator] {_failed_sym} 并行评估失败: {_e}")
                    results[_failed_sym] = OrchestratorDecision(
                        symbol=_failed_sym, timestamp=time.time()
                    )

        # 补全缺失的 symbol（超时未返回的）
        for sym in symbols:
            if sym not in results:
                logger.warning(f"[MTOrchestrator] {sym} 评估超时，使用空决策")
                results[sym] = OrchestratorDecision(symbol=sym, timestamp=time.time())

        return results

    def get_last_decision(self, symbol: str) -> Optional[OrchestratorDecision]:
        return self._last_decisions.get(symbol)

    # ════════════════════════ 三层分析 ════════════════════════
    #
    # 2026-07-06 说明（审查 3 #3，统一 tier→timeframe 映射）：下面三个 _analyze_*_term
    # 函数里出现的 "1d"/"1w"（长）、"1h"/"4h"（中）、"5m"/"15m"/"1m"（短）等具体周期
    # 字符串，语义上对应 backend/config/tier_timeframe_map.py 的
    # long.confirm=[1d,1w] / mid.primary+confirm≈[1h,4h] / short.primary+confirm≈[15m,5m,1m]。
    # 之所以没有像 trend_classifier.py / strategy_coordinator.py 那样把这些字面量
    # 换成 TIER_TIMEFRAME_MAP[...] 取值，是因为这里每个周期字符串都直接嵌在具体的
    # 指标计算调用里（classify_from_indicators(ind, kl, "1d", symbol) 这类），
    # 而不是先聚合成一个周期列表再统一处理——生搬硬套改成变量反而会让这段"1d
    # 定方向、1w 做慢周期确认、4h 优先于 1h"的分层递进逻辑变得难以阅读，且没有
    # 实际收益（这里不存在 trend_classifier 那种"函数从未被调用、参数名硬编码
    # 导致语义混乱"的 bug）。H1/H2 两条真正判断周期方向冲突的硬约束（在
    # _apply_frequency_constraints 里）已经改为通过 d.mid_view/d.short_view
    # 比较，不再依赖这里的字面量周期字符串。
    def _analyze_long_term(self, symbol: str, snapshot) -> TimeframeView:
        view = TimeframeView("long")
        # ── 宏观周期心智（慢变量基底，优先于 tick 级噪声）──
        _macro_state = None
        _macro_blocks_fgi_bullish = False
        try:
            from backend.services.macro_regime_service import (
                macro_regime_service, _PHASE_TO_BIAS,
            )
            _macro_state = macro_regime_service.get_state("GLOBAL")
            _phase_bias = _PHASE_TO_BIAS.get(_macro_state.cycle_phase, "neutral")
            if _phase_bias != "neutral":
                view.bias = _phase_bias
                view.confidence = max(view.confidence, _macro_state.phase_confidence)
                view.details = (
                    f"宏观周期={_macro_state.cycle_phase}({_macro_state.phase_confidence:.0%})"
                    f" 约束={_macro_state.direction_constraint}"
                )
            if _macro_state.cycle_phase == "decline" and _macro_state.phase_confidence >= 0.6:
                _macro_blocks_fgi_bullish = True
        except Exception as _mrs_err:
            logger.debug(f"[MTOrchestrator] 宏观周期心智读取跳过: {_mrs_err}")

        try:
            # 优先读取 per-symbol 长线规划结果
            per_sym = getattr(snapshot, 'per_symbol_planning', {}) if snapshot else {}
            sym_plan = per_sym.get(symbol)
            if sym_plan is not None:
                cycle = sym_plan.market_cycle.value if hasattr(sym_plan.market_cycle, 'value') else str(sym_plan.market_cycle)
                bias = sym_plan.position_bias or "neutral"
                confidence = sym_plan.cycle_confidence or 0.0
            elif snapshot and hasattr(snapshot, 'strategy'):
                s = snapshot.strategy
                cycle = getattr(s, 'market_cycle', 'unknown')
                bias = getattr(s, 'position_bias', 'neutral')
                confidence = getattr(s, 'cycle_confidence', 0.0)
            else:
                cycle, bias, confidence = "unknown", "neutral", 0.0

            view.bias = self._normalize_bias(bias)
            view.confidence = confidence
            if sym_plan and sym_plan.key_levels:
                view.key_levels = {
                    "support": sym_plan.key_levels.get("nearest_support", 0),
                    "resistance": sym_plan.key_levels.get("nearest_resistance", 0),
                }
            elif snapshot and hasattr(snapshot, 'strategy'):
                view.key_levels = {
                    "support": getattr(snapshot.strategy, 'key_support', 0),
                    "resistance": getattr(snapshot.strategy, 'key_resistance', 0),
                }
            view.details = f"周期={cycle}, 偏向={bias}, 置信={confidence:.0%}"

            # ADX/TrendState 加强：用 1d 级别趋势修正置信度，并用 1w 做慢周期确认
            try:
                from backend.services.trend_classifier import classify_from_indicators
                ind = snapshot.indicators.get(symbol, {}) if snapshot and hasattr(snapshot, 'indicators') else {}
                kl = snapshot.klines if snapshot and hasattr(snapshot, 'klines') else None
                ts_1d = classify_from_indicators(ind, kl, "1d", symbol)
                if ts_1d.strength in ("strong", "moderate") and ts_1d.direction != "neutral":
                    view.confidence = max(view.confidence, 0.6)
                    if ts_1d.direction == "up":
                        view.bias = "bullish"
                    else:
                        view.bias = "bearish"
                    view.details += f" | 1d趋势={ts_1d.direction}/{ts_1d.strength}(ADX={ts_1d.adx:.0f})"
                elif ts_1d.adx < 15:
                    view.confidence = min(view.confidence, 0.25)
                    view.details += f" | 1d无趋势(ADX={ts_1d.adx:.0f})→置信压低"

                ts_1w = classify_from_indicators(ind, kl, "1w", symbol)
                if ts_1w.strength in ("strong", "moderate") and ts_1w.direction != "neutral":
                    weekly_bias = "bullish" if ts_1w.direction == "up" else "bearish"
                    if view.bias == weekly_bias:
                        view.confidence = min(0.85, view.confidence + 0.08)
                        view.details += f" | 1w确认={ts_1w.direction}/{ts_1w.strength}(ADX={ts_1w.adx:.0f})"
                    elif view.bias in ("bullish", "bearish"):
                        view.confidence = round(max(0.22, view.confidence * 0.75), 3)
                        view.details += f" | 1w反向={ts_1w.direction}/{ts_1w.strength}(ADX={ts_1w.adx:.0f})→长期降权"
                    elif view.confidence < 0.35:
                        view.bias = weekly_bias
                        view.confidence = max(view.confidence, 0.45)
                        view.details += f" | 1w慢周期锚={ts_1w.direction}/{ts_1w.strength}(ADX={ts_1w.adx:.0f})"
            except Exception:
                pass

            # 用情报信号对长期做 per-symbol 修正
            if (view.bias == "neutral" or view.confidence < 0.3) and not _macro_blocks_fgi_bullish:
                try:
                    intel = self._get_intel_signal(symbol)
                    if intel is not None:
                        fgi = intel.fear_greed_index
                        intel_dir = intel.direction

                        p = self._params
                        # 2026-06-18: 恐贪指数(FGI)是公认的反向指标 —— 极恐=底部该找机会做多，
                        # 极贪=顶部该减仓。原逻辑把它当顺势指标用（极恐→偏空、极贪→偏多），
                        # 是典型的高位接盘、低位砍仓。现按反向指标修正。
                        if fgi < p["long_fgi_extreme_fear"]:
                            view.bias = "bullish"
                            view.confidence = max(view.confidence, 0.5)
                            view.details += f" | 恐贪={fgi:.0f}(极恐→偏多/抄底试探,反向指标)"
                        elif fgi > p["long_fgi_extreme_greed"]:
                            view.bias = "bearish"
                            view.confidence = max(view.confidence, 0.5)
                            view.details += f" | 恐贪={fgi:.0f}(极贪→偏空/减仓,反向指标)"
                        elif fgi < p["long_fgi_fear"]:
                            view.bias = "bullish"
                            view.confidence = max(view.confidence, 0.35)
                            view.details += f" | 恐贪={fgi:.0f}(恐惧→偏多,反向指标)"
                        elif fgi > p["long_fgi_greed"]:
                            view.bias = "bearish"
                            view.confidence = max(view.confidence, 0.35)
                            view.details += f" | 恐贪={fgi:.0f}(贪婪→偏空,反向指标)"
                        elif intel_dir in ("bullish", "bearish") and intel.confidence > p["long_intel_min_conf"]:
                            view.bias = intel_dir
                            view.confidence = max(view.confidence, 0.3)
                            view.details += f" | 情报={intel_dir}({intel.confidence}%)"
                except Exception:
                    pass
            elif _macro_blocks_fgi_bullish and (view.bias == "neutral" or view.confidence < 0.3):
                view.details += " | FGI修正跳过(宏观decline高置信)"

            # Fix 17b/17c: 链上/期权数据辅助长线判断（领先指标）
            # 期权偏斜(put_iv/call_iv >1=恐慌)和链上活跃度是趋势的领先信号
            try:
                _opt_skew = ind.get("options_skew", 0) if isinstance(ind, dict) else 0
                _put_call = ind.get("put_call_ratio", 0) if isinstance(ind, dict) else 0
                if _opt_skew > 1.1:
                    # 看跌IV显著高于看涨 → 机构在大量买入下行保护 → 看空
                    if view.bias == "neutral":
                        view.bias = "bearish"
                        view.confidence = max(view.confidence, 0.35)
                    view.details += f" | 期权偏斜={_opt_skew:.2f}(看跌保护需求强→偏空)"
                elif _opt_skew > 0 and _opt_skew < 0.9:
                    # 看涨IV更高 → 市场贪婪 → 反向偏空
                    if view.bias == "neutral":
                        view.bias = "bullish"
                        view.confidence = max(view.confidence, 0.3)
                    view.details += f" | 期权偏斜={_opt_skew:.2f}(看涨投机→偏多)"
                if _put_call > 1.3:
                    view.details += f" | P/C={_put_call:.2f}(对冲需求高→谨慎)"
            except Exception:
                pass

            # ── 长线专属：OI趋势/funding极端/多空比/funding-OI背离（天级宏观资金）──
            try:
                intel = self._get_intel_signal(symbol)
                if intel is not None:
                    # funding rate 极端 → 反转信号（长线核心）
                    if intel.funding and abs(intel.funding.rate) > 0.0005:
                        if intel.funding.rate > 0.0008:
                            # 极正 funding = 多头极度拥挤 → 长线看空
                            if view.bias == "neutral":
                                view.bias = "bearish"
                                view.confidence = max(view.confidence, 0.35)
                            view.details += f" | funding极正({intel.funding.rate*100:.3f}%)→反转风险"
                        elif intel.funding.rate < -0.0008:
                            # 极负 funding = 空头极度拥挤 → 长线看多
                            if view.bias == "neutral":
                                view.bias = "bullish"
                                view.confidence = max(view.confidence, 0.35)
                            view.details += f" | funding极负({intel.funding.rate*100:.3f}%)→反弹可能"
                    # OI 变化趋势（资金流入/流出确认趋势）
                    if intel.oi and abs(intel.oi.oi_change_pct) > 0.03:
                        _oi_sig = intel.oi.signal if hasattr(intel.oi, 'signal') else "neutral"
                        if _oi_sig in ("bullish", "bearish") and view.bias == _oi_sig:
                            view.confidence = min(1.0, view.confidence + 0.08)
                            view.details += f" | OI{intel.oi.oi_change_pct:+.1%}确认趋势"
                    # 多空比（大户持仓定位）
                    if intel.long_short_ratio and abs(intel.long_short_ratio - 1.0) > 0.3:
                        if intel.long_short_ratio > 1.5:
                            view.details += f" | 多空比={intel.long_short_ratio:.2f}(多头密集)"
                        elif intel.long_short_ratio < 0.7:
                            view.details += f" | 多空比={intel.long_short_ratio:.2f}(空头密集)"
                # funding-OI 背离（建仓领先指标，长线最有价值）
                from backend.services.crypto_alpha_signals import crypto_alpha
                _foid = crypto_alpha.funding_oi_divergence(symbol)
                if _foid.available and _foid.direction != "neutral" and _foid.strength > 0.3:
                    _foid_bias = "bullish" if _foid.direction == "long" else "bearish"
                    if view.bias == "neutral":
                        view.bias = _foid_bias
                        view.confidence = max(view.confidence, 0.3)
                        view.details += f" | funding-OI背离→{_foid_bias}({_foid.note[:30]})"
                    elif view.bias == _foid_bias:
                        view.confidence = min(1.0, view.confidence + 0.06)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[MTOrchestrator] 长期分析异常: {e}")

        # 宏观高置信阶段锚定：decline/markup 不可被 tick 级信号翻向
        if _macro_state and _macro_state.phase_confidence >= 0.6:
            try:
                from backend.services.macro_regime_service import _PHASE_TO_BIAS
                _anchor_bias = _PHASE_TO_BIAS.get(_macro_state.cycle_phase, "neutral")
                if _anchor_bias == "bearish" and view.bias == "bullish":
                    view.bias = "bearish"
                    view.confidence = max(view.confidence, _macro_state.phase_confidence)
                    view.details += " | [宏观锚定] decline否决看多"
                elif _anchor_bias == "bullish" and view.bias == "bearish":
                    if _macro_state.cycle_phase == "markup":
                        view.bias = "bullish"
                        view.confidence = max(view.confidence, _macro_state.phase_confidence)
                        view.details += " | [宏观锚定] markup否决看空"
            except Exception:
                pass

        if view.bias in ("bullish", "bearish") and view.confidence < 0.18:
            view.confidence = round(max(view.confidence, 0.22), 3)

        return view

    def _analyze_mid_term(self, symbol: str, snapshot) -> TimeframeView:
        view = TimeframeView("mid")
        try:
            ind = {}
            if snapshot and hasattr(snapshot, 'indicators'):
                ind = snapshot.indicators.get(symbol, {}) or {}
            try:
                from backend.config.settings import STRICT_DATA_GATE
                from backend.services.data_readiness_gate import indicators_are_real
                if STRICT_DATA_GATE and not indicators_are_real(ind):
                    view.bias = "neutral"
                    view.confidence = 0.0
                    view.details = "DATA_MISSING:无真实指标，禁止RSI=50/MACD=0推断中期方向"
                    return view
            except Exception:
                pass

            # 中期优先使用 4h 动量（真正的中周期）；4h 缺失时回退 1h
            _rsi_4h = ind.get("rsi_4h")
            _macd_4h = ind.get("macd_4h")
            rsi = float(_rsi_4h) if _rsi_4h is not None else float(ind.get("rsi", 50) or 50)
            macd = float(_macd_4h) if _macd_4h is not None else float(ind.get("macd", 0) or 0)
            _mom_src = "4h" if _rsi_4h is not None else "1h"

            # 4h TrendState 作为主判断
            ts_4h = None
            try:
                from backend.services.trend_classifier import classify_from_indicators
                kl = snapshot.klines if snapshot and hasattr(snapshot, 'klines') else None
                ts_4h = classify_from_indicators(ind, kl, "4h", symbol)
            except Exception:
                pass

            p = self._params

            if ts_4h and ts_4h.strength in ("strong", "moderate"):
                # 4h 趋势明确时以 TrendState 为主
                if ts_4h.direction == "up":
                    view.bias = "bullish"
                    view.confidence = 0.6 if ts_4h.strength == "strong" else 0.45
                elif ts_4h.direction == "down":
                    view.bias = "bearish"
                    view.confidence = 0.6 if ts_4h.strength == "strong" else 0.45
                else:
                    view.bias = "neutral"
                    view.confidence = p["mid_conf_neutral"]
                # RSI/MACD 作为辅助增减
                if view.bias == "bullish" and rsi > 55 and macd > 0:
                    view.confidence = min(0.85, view.confidence + 0.15)
                elif view.bias == "bearish" and rsi < 45 and macd < 0:
                    view.confidence = min(0.85, view.confidence + 0.15)
                elif (view.bias == "bullish" and rsi < 40) or (view.bias == "bearish" and rsi > 60):
                    view.confidence *= 0.7
            else:
                # 4h 无明确趋势 → 退回 RSI + MACD 判断
                if rsi > p["mid_rsi_bull"] and macd > 0:
                    view.bias = "bullish"
                    view.confidence = min(p["mid_conf_strong"], (rsi - 45) / 40 + abs(macd) * 8)
                elif rsi < p["mid_rsi_bear"] and macd < 0:
                    view.bias = "bearish"
                    view.confidence = min(p["mid_conf_strong"], (55 - rsi) / 40 + abs(macd) * 8)
                elif rsi > p["mid_rsi_weak_bull"] and macd > 0:
                    view.bias = "bullish"
                    view.confidence = p["mid_conf_weak"]
                elif rsi < p["mid_rsi_weak_bear"] and macd < 0:
                    view.bias = "bearish"
                    view.confidence = p["mid_conf_weak"]
                else:
                    view.bias = "neutral"
                    view.confidence = p["mid_conf_neutral"]

            adx_4h = ind.get("adx_4h", 0)
            view.details = f"RSI={rsi:.1f}({_mom_src}), MACD={macd:.4f}, 4hADX={adx_4h:.0f}"
            if ts_4h:
                view.details += f", 4h趋势={ts_4h.direction}/{ts_4h.strength}"

            # ── 1h 动量共振确认（swing 三屏之"择时屏"）──
            # 4h 为主时，用 1h 动量做同向增强 / 背离降权；4h 缺失回退时 1h 已是主判断，跳过
            if _mom_src == "4h" and view.bias in ("bullish", "bearish"):
                _rsi_1h = float(ind.get("rsi", 50) or 50)
                _macd_1h = float(ind.get("macd", 0) or 0)
                _h1_bull = _rsi_1h > 52 and _macd_1h > 0
                _h1_bear = _rsi_1h < 48 and _macd_1h < 0
                if (view.bias == "bullish" and _h1_bull) or (view.bias == "bearish" and _h1_bear):
                    view.confidence = min(0.9, view.confidence + 0.08)
                    view.details += f" | 1h共振({_rsi_1h:.0f})"
                elif (view.bias == "bullish" and _h1_bear) or (view.bias == "bearish" and _h1_bull):
                    view.confidence = round(max(0.22, view.confidence * 0.85), 3)
                    view.details += f" | 1h背离({_rsi_1h:.0f})→降权"

            # ── 1d 大势背景校验（swing 三屏之"趋势屏"：顺大势加信心，逆强大势降权）──
            # 不夺长期层主导权，仅对中期做顺势/逆势的信心增减（逆势不禁止，交给门控/风控）
            try:
                from backend.services.trend_classifier import classify_from_indicators as _cfi_d1
                _kl_d1 = snapshot.klines if snapshot and hasattr(snapshot, 'klines') else None
                _ts_d1 = _cfi_d1(ind, _kl_d1, "1d", symbol)
                if _ts_d1.direction != "neutral" and _ts_d1.strength in ("strong", "moderate") and view.bias in ("bullish", "bearish"):
                    _d1_bias = "bullish" if _ts_d1.direction == "up" else "bearish"
                    if view.bias == _d1_bias:
                        view.confidence = min(0.9, view.confidence + 0.10)
                        view.details += f" | 1d顺大势={_ts_d1.direction}/{_ts_d1.strength}"
                    else:
                        _penalty = 0.6 if _ts_d1.strength == "strong" else 0.75
                        view.confidence = round(max(0.20, view.confidence * _penalty), 3)
                        view.details += f" | 1d逆大势={_ts_d1.direction}/{_ts_d1.strength}→降权"
            except Exception:
                pass

            # 情报信号融合 — 让 IntelligenceSignalEngine 的综合研判影响中期
            try:
                intel = self._get_intel_signal(symbol)
                if intel is not None:
                    intel_dir = intel.direction
                    intel_conf = intel.confidence / 100.0

                    if intel_dir in ("bullish", "bearish") and intel_conf > p["intel_fusion_min_conf"]:
                        if view.bias == "neutral":
                            view.bias = intel_dir
                            view.confidence = max(view.confidence, p["intel_fusion_neutral_boost"] + intel_conf * 0.5)
                            view.details += f" | 情报={intel_dir}({intel.confidence}%)"
                        elif view.bias == intel_dir:
                            view.confidence = min(1.0, view.confidence + p["intel_fusion_agree_boost"] + intel_conf * 0.3)
                            view.details += f" | 情报共振"
                        else:
                            view.confidence *= p["intel_fusion_conflict_mult"]
                            view.details += f" | 情报矛盾({intel_dir})"
            except Exception:
                pass

            # 合约数据补充
            if snapshot and hasattr(snapshot, 'derivatives_snapshot'):
                deriv = snapshot.derivatives_snapshot.get(symbol, {})
                if deriv.get("signal") == "bullish":
                    view.confidence = min(1.0, view.confidence + 0.1)
                elif deriv.get("signal") == "bearish":
                    if view.bias == "bullish":
                        view.confidence *= 0.7
                    elif view.bias == "neutral":
                        view.bias = "bearish"
                        view.confidence += 0.1

            # ── 币圈深度数据注入（2026-06-25 升级）──
            # 原代码只用纯技术指标（RSI/MACD/4h趋势），震荡市永远 neutral(0%)
            # 现注入 crypto_alpha + intel + 市场状态，让中线有更丰富的判断依据
            try:
                from backend.services.crypto_alpha_signals import crypto_alpha
                _cab = crypto_alpha.get_bundle(symbol)
                # 清算磁吸方向影响中线 bias
                if _cab.liquidation_magnet.available and _cab.liquidation_magnet.direction != "neutral":
                    _lm_dir = "bullish" if _cab.liquidation_magnet.direction == "long" else "bearish"
                    _lm_str = _cab.liquidation_magnet.strength
                    if view.bias == "neutral" and _lm_str > 0.5:
                        view.bias = _lm_dir
                        view.confidence = max(view.confidence, _lm_str * 0.5)
                        view.details += f" | 清算磁吸→{_lm_dir}({_cab.liquidation_magnet.severity})"
                    elif view.bias == _lm_dir:
                        view.confidence = min(1.0, view.confidence + 0.1)
                        view.details += f" | 清算磁吸共振"
                # CVD 方向
                if _cab.cvd_pressure.available and _cab.cvd_pressure.direction != "neutral" and _cab.cvd_pressure.strength > 0.3:
                    _cvd_dir = "bullish" if _cab.cvd_pressure.direction == "long" else "bearish"
                    if view.bias == "neutral":
                        view.bias = _cvd_dir
                        view.confidence = max(view.confidence, 0.25)
                        view.details += f" | CVD→{_cvd_dir}"
                    elif view.bias == _cvd_dir:
                        view.confidence = min(1.0, view.confidence + 0.08)
                # funding-OI 背离
                if _cab.funding_oi_divergence.available and _cab.funding_oi_divergence.direction != "neutral" and _cab.funding_oi_divergence.strength > 0.3:
                    _foid_dir = "bullish" if _cab.funding_oi_divergence.direction == "long" else "bearish"
                    if view.bias == "neutral":
                        view.bias = _foid_dir
                        view.confidence = max(view.confidence, 0.3)
                        view.details += f" | funding-OI背离→{_foid_dir}"
            except Exception:
                pass

            # ── 中线专属：funding/OI/鲸鱼/贪婪指数（小时级资金流向）──
            try:
                intel = self._get_intel_signal(symbol)
                if intel is not None:
                    # funding rate 拥挤度
                    if intel.funding and intel.funding.signal in ("bullish", "bearish"):
                        _fund_dir = "bullish" if intel.funding.signal == "bullish" else "bearish"
                        if view.bias == "neutral":
                            view.bias = _fund_dir
                            view.confidence = max(view.confidence, 0.3)
                            view.details += f" | funding={intel.funding.regime}"
                        elif view.bias == _fund_dir:
                            view.confidence = min(1.0, view.confidence + 0.08)
                        else:
                            view.confidence *= 0.85
                            view.details += f" | funding矛盾({intel.funding.regime})"
                    # OI 变化（资金流入/流出）
                    if intel.oi and abs(intel.oi.oi_change_pct) > 0.02:
                        _oi_dir = intel.oi.signal if hasattr(intel.oi, 'signal') else "neutral"
                        if _oi_dir in ("bullish", "bearish"):
                            if view.bias == _oi_dir:
                                view.confidence = min(1.0, view.confidence + 0.06)
                                view.details += f" | OI{intel.oi.oi_change_pct:+.1%}共振"
                            elif view.bias == "neutral":
                                view.bias = _oi_dir
                                view.confidence = max(view.confidence, 0.25)
                    # 鲸鱼方向
                    if abs(intel.whale_direction) > 0.4:
                        _whale_dir = "bullish" if intel.whale_direction > 0 else "bearish"
                        if view.bias == _whale_dir:
                            view.confidence = min(1.0, view.confidence + 0.05)
                            view.details += f" | 鲸鱼{intel.whale_direction:+.2f}共振"
                    # 贪婪指数（中线参考，非主导）
                    if intel.fear_greed_index < 25:
                        view.details += f" | 极恐({intel.fear_greed_index:.0f})"
                    elif intel.fear_greed_index > 75:
                        view.details += f" | 极贪({intel.fear_greed_index:.0f})"
            except Exception:
                pass

            # 市场状态确认：trending 时中线信号更可信
            try:
                import pandas as _pd
                from backend.services.market_regime import MarketRegimeClassifier as _MRC
                for _tf_reg in ["1h", "4h"]:
                    # 2026-07-06 整改（unified_data_pool 全量整合 · MLTO 编排取数）：
                    # 本块此前无视传入的 snapshot 直接 get_klines_from_db 重拉，与本函数其余
                    # 部分（走 snapshot.indicators）及 _inject_regime（1256 行已是"快照优先"）
                    # 口径不一致——同一次 evaluate 内 regime 判定可能基于与 bias 不同时点的 K 线。
                    # 改为与同文件既有约定一致：快照优先、缺失/过薄回退 DB（行为向后兼容）。
                    _kl_reg = None
                    if snapshot is not None and hasattr(snapshot, "klines"):
                        _df_reg = snapshot.klines.get((symbol, _tf_reg))
                        if _df_reg is not None and len(_df_reg) >= 50:
                            _kl_reg = _df_reg
                    if _kl_reg is None:
                        from backend.services.kline_data_service import kline_service as _ks_reg
                        _kl_reg = _ks_reg.get_klines_from_db(symbol, _tf_reg, count=100)
                    if _kl_reg is not None and len(_kl_reg) >= 50:
                        _cls_reg = _MRC().classify(_pd.DataFrame(_kl_reg))
                        _regime_reg = _cls_reg.regime.value if hasattr(_cls_reg.regime, "value") else str(_cls_reg.regime)
                        if "trending" in _regime_reg and view.bias != "neutral":
                            view.confidence = min(1.0, view.confidence + 0.1)
                            view.details += f" | regime={_regime_reg}(趋势确认)"
                        elif "ranging" in _regime_reg:
                            view.confidence *= 0.7
                            view.details += f" | regime={_regime_reg}(震荡削弱)"
                        break
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[MTOrchestrator] 中期分析异常: {e}")
        return view

    def _analyze_short_term(self, symbol: str, snapshot) -> TimeframeView:
        """短期分析 — 使用每个 symbol 的 15m 指标 + 情报信号 + 中期信号回退"""
        view = TimeframeView("short")
        try:
            ind = {}
            if snapshot and hasattr(snapshot, 'indicators'):
                ind = snapshot.indicators.get(symbol, {}) or {}
            try:
                from backend.config.settings import STRICT_DATA_GATE
                from backend.services.data_readiness_gate import indicators_are_real
                if STRICT_DATA_GATE and not indicators_are_real(ind):
                    view.bias = "neutral"
                    view.confidence = 0.0
                    view.details = "DATA_MISSING:无真实指标，禁止短期假方向"
                    return view
            except Exception:
                pass

            rsi = 50.0
            macd = 0.0
            ema_trend = 0.0
            has_short_indicators = False
            if ind:
                rsi = float(ind.get("short_rsi", ind.get("rsi", 50)) or 50)
                macd = float(ind.get("short_macd", ind.get("macd", 0)) or 0)
                ema_trend = float(ind.get("short_ema_trend", ind.get("ema_trend", 0)) or 0)
                has_short_indicators = (
                    "short_rsi" in ind or "short_macd" in ind or "short_ema_trend" in ind
                )

            score = 0.0
            details_parts = [f"RSI={rsi:.1f}, MACD={macd:.4f}, EMA={ema_trend:+.4f}"]
            if not has_short_indicators:
                details_parts.append("(用1h指标回退)")

            # =============================================
            # P0: 高阶K线衍生特征评分 (F1~F12)
            # =============================================
            _ho = self._extract_higher_order_features(ind)
            _ho_score = 0.0
            _ho_parts = []

            # F7: trend_efficiency — 趋势效率高 → 趋势信号更可信
            if _ho.get("trend_efficiency", 0) > 0.55:
                _ho_score += 0.12
                _ho_parts.append(f"高效趋势({_ho['trend_efficiency']:.2f})")
            elif _ho.get("trend_efficiency", 0) < 0.25:
                _ho_parts.append(f"低效震荡({_ho['trend_efficiency']:.2f})")
                _ho_score -= 0.05  # 震荡市减分

            # F8: volume_climax — 放量≥1.5x → 爆发信号
            if _ho.get("volume_climax", 1.0) >= 2.0:
                _ho_score += 0.15
                _ho_parts.append(f"巨量爆发({_ho['volume_climax']:.1f}x)")
            elif _ho.get("volume_climax", 1.0) >= 1.5:
                _ho_score += 0.08
                _ho_parts.append(f"放量({_ho['volume_climax']:.1f}x)")
            elif _ho.get("volume_climax", 1.0) < 0.5:
                _ho_score -= 0.05
                _ho_parts.append(f"缩量({_ho['volume_climax']:.1f}x)")

            # F1: body_ratio — 实体占比高 → 趋势方向坚定
            if _ho.get("body_ratio", 0) > 0.6:
                _ho_score += 0.10
                _ho_parts.append(f"坚定实体({_ho['body_ratio']:.2f})")

            # F4: doji_score — 十字星 → 犹豫/反转信号
            if _ho.get("doji_score", 0) > 0.85:
                _ho_score -= 0.10
                _ho_parts.append(f"十字星犹豫({_ho['doji_score']:.2f})")
                # 十字星出现时削弱当前 bias 置信度
                if view.bias != "neutral":
                    score *= 0.7

            # F5: volume_price_corr — 量价正相关 + 趋势 → 验证趋势质量
            if abs(_ho.get("volume_price_corr", 0)) > 0.4:
                _ho_score += 0.06
                _ho_parts.append(f"量价相关({_ho['volume_price_corr']:+.2f})")

            # F11: rsi_divergence — RSI背离 → 强反转信号
            if _ho.get("rsi_divergence", 0) > 0.5:
                _ho_score -= 0.15
                _ho_parts.append("RSI背离!(反转)")
                if view.bias == "bullish":
                    view.bias = "bearish"
                    score *= 0.6
                elif view.bias == "bearish":
                    view.bias = "bullish"
                    score *= 0.6

            # F12: volume_imbalance — 买卖失衡度
            if _ho.get("volume_imbalance", 0) > 0.3:
                _ho_score += 0.08
                _ho_parts.append(f"买方主导({_ho['volume_imbalance']:+.2f})")
                if view.bias == "neutral":
                    view.bias = "bullish"
            elif _ho.get("volume_imbalance", 0) < -0.3:
                _ho_score += 0.08
                _ho_parts.append(f"卖方主导({_ho['volume_imbalance']:+.2f})")
                if view.bias == "neutral":
                    view.bias = "bearish"

            # F9: price_acceleration — 加速时增强信号
            if abs(_ho.get("price_acceleration", 0)) > 0.03:
                _ho_score += 0.06
                _ho_parts.append(f"加速({_ho['price_acceleration']:+.4f})")

            # F2/F3: 影线比例 — 上下影线不对称
            _upper_sh = _ho.get("upper_shadow_ratio", 0)
            _lower_sh = _ho.get("lower_shadow_ratio", 0)
            if _upper_sh > 0.5 and _lower_sh < 0.2:
                _ho_score -= 0.06
                _ho_parts.append("上影压力")
            elif _lower_sh > 0.5 and _upper_sh < 0.2:
                _ho_score += 0.06
                _ho_parts.append("下影支撑")

            score += _ho_score
            if _ho_parts:
                details_parts.append("高阶:" + ",".join(_ho_parts[:5]))

            # =============================================
            # P0: VPVR v2 — 成交量分布近端S/R分析
            # =============================================
            _vpvr_scored = False
            try:
                from backend.services.unified_data_pool import compute_volume_profile_v2
                _vp_data = compute_volume_profile_v2(symbol, days=3, bucket_count=50, va_pct=0.70)
                if _vp_data and not _vp_data.get("error") and _vp_data.get("poc", 0) > 0:
                    _price_now = float(ind.get("current_price", 0) or 0)
                    if _price_now <= 0:
                        _price_now = float(ind.get("last_price", 0) or 0)
                    if _price_now > 0:
                        _poc = float(_vp_data.get("poc", 0))
                        _vah = float(_vp_data.get("vah", 0))
                        _val = float(_vp_data.get("val", 0))
                        _in_va = bool(_vp_data.get("current_in_va", False))
                        _vpvr_scored = True
                        _vpvr_parts = []

                        # 价格在VA内 → 正常区间，中性
                        if _in_va:
                            _vpvr_parts.append("在VA内")
                        # 价格接近POC → 强支撑/阻力确认
                        if _poc > 0 and abs(_price_now - _poc) / max(_poc, 1e-8) < 0.02:
                            _vpvr_parts.append("近POC(强S/R)")
                            score += 0.08
                        # 价格接近VAH → 阻力位，做多谨慎
                        if _vah > 0 and _price_now > _vah * 0.98:
                            if view.bias == "bullish":
                                score -= 0.08
                                _vpvr_parts.append("近VAH阻力")
                            _vpvr_parts.append(f"超VAH({_price_now:.0f}>{_vah:.0f})")
                        # 价格接近VAL → 支撑位，做空谨慎
                        if _val > 0 and _price_now < _val * 1.02:
                            if view.bias == "bearish":
                                score -= 0.08
                                _vpvr_parts.append("近VAL支撑")
                            _vpvr_parts.append(f"破VAL({_price_now:.0f}<{_val:.0f})")

                        if _vpvr_parts:
                            details_parts.append("VPVR:" + ",".join(_vpvr_parts))
            except Exception:
                pass

            # EMA 趋势方向（降低门槛从0.002到0.001）
            if ema_trend > 0.001:
                score += 0.15
                if view.bias == "neutral":
                    view.bias = "bullish"
                details_parts.append(f"EMA趋势偏多")
            elif ema_trend < -0.001:
                score += 0.15
                if view.bias == "neutral":
                    view.bias = "bearish"
                details_parts.append(f"EMA趋势偏空")

            # RSI + MACD 综合（降低门槛，允许更多信号触发）
            if rsi > 65 and macd > 0:
                score += 0.3
                details_parts.append("RSI偏高+MACD正→偏多")
                view.bias = "bullish"
            elif rsi < 35 and macd < 0:
                score += 0.3
                details_parts.append("RSI偏低+MACD负→偏空")
                view.bias = "bearish"
            # 降低门槛：弱信号也能积累分数
            elif rsi > 55 and macd > 0:
                score += 0.15
                if view.bias == "neutral":
                    view.bias = "bullish"
            elif rsi < 45 and macd < 0:
                score += 0.15
                if view.bias == "neutral":
                    view.bias = "bearish"
            # 新增：单一指标触发
            elif rsi > 60:
                score += 0.1
                if view.bias == "neutral":
                    view.bias = "bullish"
            elif rsi < 40:
                score += 0.1
                if view.bias == "neutral":
                    view.bias = "bearish"

            # 情报引擎补充（鲸鱼 + 资金费率 + 订单流）
            try:
                intel = self._get_intel_signal(symbol)
                if intel is not None:
                    whale = intel.whale_direction
                    funding_signal = intel.funding.signal if intel.funding else "neutral"
                    intel_dir = intel.direction
                    intel_conf = intel.confidence / 100.0

                    wt = self._params["short_whale_threshold"]
                    if whale > wt:
                        score += 0.15
                        details_parts.append(f"鲸鱼偏多({whale:.2f})")
                        if view.bias == "neutral":
                            view.bias = "bullish"
                    elif whale < -wt:
                        score += 0.15
                        details_parts.append(f"鲸鱼偏空({whale:.2f})")
                        if view.bias == "neutral":
                            view.bias = "bearish"

                    if funding_signal == "bearish":
                        score += 0.1
                        details_parts.append("费率偏空(空头拥挤)")
                    elif funding_signal == "bullish":
                        score += 0.1
                        details_parts.append("费率偏多")

                    if intel_dir in ("bullish", "bearish") and intel_conf > 0.35:
                        if view.bias == "neutral":
                            view.bias = intel_dir
                            score += 0.2
                            details_parts.append(f"情报={intel_dir}({intel.confidence}%)")
                        elif view.bias == intel_dir:
                            score += 0.15
                            details_parts.append("情报共振")
                        else:
                            score *= 0.6
                            details_parts.append(f"情报矛盾({intel_dir})")
            except Exception:
                pass

            # 如果还有 ShortTermTactician 的结果，也参考
            if snapshot and hasattr(snapshot, 'strategy'):
                s = snapshot.strategy
                tact_action = getattr(s, 'tactical_action', 'wait')
                tact_conf = getattr(s, 'tactical_confidence', 0.0)
                if tact_action in ('enter_long', 'add_long') and tact_conf > 0.3:
                    score += 0.15
                    if view.bias == "neutral":
                        view.bias = "bullish"
                    details_parts.append(f"战术器={tact_action}")
                elif tact_action in ('enter_short', 'add_short') and tact_conf > 0.3:
                    score += 0.15
                    if view.bias == "neutral":
                        view.bias = "bearish"
                    details_parts.append(f"战术器={tact_action}")

            # =============================================
            # 币圈原生 alpha（清算磁吸/CVD/订单簿失衡）—— 短线层
            # 这些是币圈永续合约独有的微观结构信号，短线最敏感。详见 crypto_alpha_signals.py
            # =============================================
            try:
                from backend.services.crypto_alpha_signals import crypto_alpha
                _cb = crypto_alpha.get_bundle(symbol)
                # 清算磁吸：方向共振加分，反向且 high 扣分
                if _cb.liquidation_magnet.available and _cb.liquidation_magnet.direction != "neutral":
                    # 映射 alpha direction(long/short) 到 bias(bullish/bearish)
                    _lm_dir = _cb.liquidation_magnet.direction
                    _lm_bias = "bullish" if _lm_dir == "long" else "bearish" if _lm_dir == "short" else "neutral"
                    if _lm_bias != "neutral":
                        if view.bias == _lm_bias:
                            score += 0.15 if _cb.liquidation_magnet.severity == "high" else 0.08
                            details_parts.append(f"清算磁吸共振({_cb.liquidation_magnet.severity})")
                        elif view.bias != "neutral":
                            # 反向且 high → 强扣分（级联清算会打断短线）
                            if _cb.liquidation_magnet.severity == "high":
                                score -= 0.20
                                details_parts.append(f"清算磁吸反向high(风险)")
                # CVD 共振
                if _cb.cvd_pressure.available and _cb.cvd_pressure.direction != "neutral" and _cb.cvd_pressure.strength > 0.4:
                    _cvd_bias = "bullish" if _cb.cvd_pressure.direction == "long" else "bearish"
                    if view.bias == _cvd_bias:
                        score += 0.10
                        details_parts.append(f"CVD共振({_cb.cvd_pressure.direction})")
                    elif view.bias != "neutral":
                        score -= 0.08
                # 订单簿失衡（OBI）— 短线即时挂单压力
                if _cb.orderbook_imbalance.available and _cb.orderbook_imbalance.direction != "neutral" and _cb.orderbook_imbalance.strength > 0.4:
                    _obi_bias = "bullish" if _cb.orderbook_imbalance.direction == "long" else "bearish"
                    if view.bias == _obi_bias:
                        score += 0.10
                        details_parts.append(f"挂单失衡共振({_cb.orderbook_imbalance.direction})")
                    elif view.bias != "neutral":
                        score -= 0.06
                        details_parts.append(f"挂单失衡反向({_cb.orderbook_imbalance.direction})")
            except Exception as _cae:
                logger.debug(f"[MTOrchestrator] 短期币圈alpha注入跳过: {_cae}")

            view.confidence = min(1.0, score)
            if view.bias == "neutral":
                # 保留弱信号分数，供 _align_tier_confidences 从 mid/long 继承
                view.confidence = min(0.35, score) if score >= 0.12 else 0.0
            if view.bias != "neutral":
                view.suggested_action = f"enter_{'long' if view.bias == 'bullish' else 'short'}"
            view.details = " | ".join(details_parts)

            from backend.config.settings import STRICT_DATA_GATE
            if (
                not STRICT_DATA_GATE
                and view.bias == "neutral"
                and snapshot
                and hasattr(snapshot, 'indicators')
            ):
                ind = snapshot.indicators.get(symbol, {}) or {}
                price_change_1h = float(ind.get("price_change_1h", 0) or 0)
                price_change_24h = float(ind.get("price_change_24h", 0) or 0)
                if price_change_1h > 0.005 or price_change_24h > 0.01:
                    view.bias = "bullish"
                    view.confidence = 0.25
                    view.suggested_action = "enter_long"
                    view.details += f" | 价格变动推断偏多(1h={price_change_1h:+.2%},24h={price_change_24h:+.2%})"
                elif price_change_1h < -0.005 or price_change_24h < -0.01:
                    view.bias = "bearish"
                    view.confidence = 0.25
                    view.suggested_action = "enter_short"
                    view.details += f" | 价格变动推断偏空(1h={price_change_1h:+.2%},24h={price_change_24h:+.2%})"

        except Exception as e:
            logger.warning(f"[MTOrchestrator] 短期分析异常: {e}")
        return view

    def _align_tier_confidences(
        self,
        decision: OrchestratorDecision,
        snapshot=None,
        symbol: str = "",
    ) -> None:
        """短期 neutral/0% 时从 mid/long 继承（仅当指标数据真实）。"""
        sv = decision.short_view
        mv = decision.mid_view
        lv = decision.long_view

        try:
            from backend.config.settings import STRICT_DATA_GATE
            if STRICT_DATA_GATE and snapshot and symbol:
                from backend.services.data_readiness_gate import assess_symbol_data
                rep = assess_symbol_data(symbol, snapshot=snapshot)
                if not rep.indicators_ok:
                    return
        except Exception:
            pass

        if sv.bias == "neutral" and sv.confidence < 0.15:
            if mv.bias in ("bullish", "bearish") and mv.confidence >= 0.20:
                sv.bias = mv.bias
                sv.confidence = round(min(0.55, mv.confidence * 0.75), 3)
                sv.details += f" | short←mid({mv.confidence:.0%})"
                # 2026-07-06 整改（审查 3 #19）：0.22 下限只在"继承"这个分支里生效——
                # 继承是主动决策要借用 mid/long 的方向，需要一个可用的置信度数值
                # 才能参与后续三层投票，不属于"抬高弱信号"。
                if sv.confidence < 0.18:
                    sv.confidence = 0.18
            elif lv.bias in ("bullish", "bearish") and lv.confidence >= 0.20:
                sv.bias = lv.bias
                sv.confidence = round(min(0.45, lv.confidence * 0.55), 3)
                sv.details += f" | short←long({lv.confidence:.0%})"
                if sv.confidence < 0.18:
                    sv.confidence = 0.18

        # 非继承场景：某个周期自己独立算出的方向若置信度仍然 <0.18，说明该周期
        # 本来就没有有效信号。此前的实现不分场景把 lv/mv/sv 弱信号统一抬到 ≥0.22
        # 让它"参与投票"，等于把噪音伪装成有效信号去影响三层协调结果；终态改为
        # 强制中性、清零置信度，不参与投票。
        for view in (lv, mv, sv):
            if view.bias in ("bullish", "bearish") and 0 < view.confidence < 0.18:
                view.bias = "neutral"
                view.confidence = 0.0

    # ════════════════════════ 情报注入 ════════════════════════

    def _inject_intelligence(self, decision: OrchestratorDecision, snapshot):
        if not snapshot:
            return

        # 情绪指数
        sent = {}
        if hasattr(snapshot, 'sentiment_index'):
            sent = snapshot.sentiment_index.get(decision.symbol, {})
        decision.sentiment_index = sent.get("index", 50)
        decision.sentiment_zone = sent.get("zone", "neutral")

    # ════════════════════════ P1-7: 市场状态注入 ════════════════════════

    def _inject_regime(self, decision: OrchestratorDecision, snapshot=None):
        """注入 MarketRegimeClassifier 分类结果到决策管线 (P1-7)

        优先从快照中读取 K 线数据，避免重复 API 调用导致超时。
        从 K线数据中检测市场状态:
        - Crash → position_scale=0, 禁止开仓
        - Ranging → position_scale=0.25, 仓位缩减至1/4
        - TrendingUp → position_scale=1.3, 仓位×1.3 (上限)
        - TrendingDown → position_scale=0.8, 适度保守
        - 其他 → position_scale=1.0 默认
        """
        try:
            from backend.services.market_regime import MarketRegimeClassifier, MarketRegime
            import pandas as pd

            _df = None

            # 优先从快照读取 1h K线（零网络延迟）
            if snapshot and hasattr(snapshot, 'klines'):
                _kline_data = snapshot.klines.get((decision.symbol, "1h"))
                if _kline_data is not None and len(_kline_data) >= 50:
                    try:
                        _df = pd.DataFrame(_kline_data)
                    except Exception:
                        pass

            # 快照无数据时回退到 API 获取（保持兼容）
            if _df is None:
                try:
                    from backend.services.market_data import get_kline_data
                    _klines = get_kline_data(
                        symbol=decision.symbol, market="CRYPTO", period="1h", count=100,
                    )
                    if _klines is not None and len(_klines) >= 50:
                        _df = pd.DataFrame(_klines)
                except Exception:
                    pass

            if _df is None or len(_df) < 20:
                decision.regime = "unknown"
                decision.regime_confidence = 0.0
                decision.position_scale = 1.0
                return

            classification = MarketRegimeClassifier().classify(_df)
            decision.regime = classification.regime.value if hasattr(classification.regime, 'value') else str(classification.regime)
            decision.regime_confidence = float(classification.confidence)

            # ── 状态 → 仓位/方向调整 ──
            regime_name = decision.regime.lower()
            if "crash" in regime_name:
                # 2026-06-18: CRASH 不再一刀切 frozen。崩盘市是做空/减多的黄金窗口，
                # 原 frozen + allowed_direction=none 把手脚绑住，在最该行动时无所作为。
                # 现改为：禁止开多（防守），但允许做空（顺势）；仓位缩到 0.5（崩盘市仓位要小但非0），
                # 由 SL/TP 硬监控兜底单笔风险，防 V 型反转。
                decision.position_scale = 0.5
                decision.allowed_direction = "short_only"
                decision.reasoning += (
                    f" | Regime=CRASH(conf={decision.regime_confidence:.0%})"
                    f"→禁多/允许做空(仓位×0.5)"
                )
            elif "ranging" in regime_name:
                # P0-2: 从 0.5 降到 0.25——历史数据震荡市 31.2% 胜率，需大幅缩减仓位
                decision.position_scale = 0.25
                decision.reasoning += (
                    f" | Regime=RANGING(conf={decision.regime_confidence:.0%})"
                    f"→仓位×0.25"
                )
            elif "trending_up" in regime_name:
                decision.position_scale = min(1.3, decision.position_scale * 1.3)
                decision.reasoning += (
                    f" | Regime=TRENDING_UP(conf={decision.regime_confidence:.0%})"
                    f"→仓位×{decision.position_scale:.1f}"
                )
            elif "trending_down" in regime_name:
                decision.position_scale = 0.8
                decision.reasoning += (
                    f" | Regime=TRENDING_DOWN(conf={decision.regime_confidence:.0%})"
                    f"→仓位×0.8"
                )
            # high_volatility / low_volatility: 保持默认 position_scale=1.0

            # P2-1: 基于 StrategyRegimeScore 历史表现进一步调整仓位
            try:
                from backend.database.connection import SessionLocal
                from sqlalchemy import text as sa_text
                _reg_db = SessionLocal()
                try:
                    _reg_row = _reg_db.execute(sa_text("""
                        SELECT AVG(win_rate) as avg_wr, SUM(sample_count) as total_n
                        FROM strategy_regime_scores
                        WHERE regime = :regime AND source = 'paper'
                          AND sample_count >= 5
                        LIMIT 1
                    """), {"regime": regime_name}).fetchone()
                    if _reg_row and _reg_row[1] and int(_reg_row[1]) >= 15:
                        _avg_wr = float(_reg_row[0] or 0.5)
                        if _avg_wr < 0.30:
                            decision.position_scale *= 0.5
                            decision.reasoning += f" | RegimeScore低胜率({ _avg_wr:.0%})→额外×0.5"
                finally:
                    _reg_db.close()
            except Exception:
                pass  # 非致命，不影响主流程

            logger.info(
                f"[MTOrchestrator] {decision.symbol}: "
                f"Regime={decision.regime}(conf={decision.regime_confidence:.0%}), "
                f"scale={decision.position_scale:.1%}"
            )

        except Exception as e:
            logger.debug(f"[MTOrchestrator] Regime注入失败 {decision.symbol}: {e}")
            decision.regime = "unknown"
            decision.regime_confidence = 0.0
            decision.position_scale = 1.0

    # ════════════════════════ 三层协调 ════════════════════════

    def _coordinate(self, decision: OrchestratorDecision):
        """加权共识评分（替代旧的硬编码查找表）。

        每个周期的 bias 转换为方向分数(-1~+1)，乘以该周期的权重和置信度，
        加权求和后得到最终共识分数，据此决定允许方向和仓位倍率。
        """
        w = self._params
        long_w = w.get("finalize_long_weight", 0.30)
        mid_w = w.get("finalize_mid_weight", 0.40)
        short_w = w.get("finalize_short_weight", 0.30)

        l_score = _BIAS_SCORE.get(decision.long_view.bias, 0.0)
        m_score = _BIAS_SCORE.get(decision.mid_view.bias, 0.0)
        s_score = _BIAS_SCORE.get(decision.short_view.bias, 0.0)

        # 只对非中性视图参与权重计算，避免 neutral(score=0) 稀释信号强度
        weighted_sum = 0.0
        total_weight = 0.0
        if l_score != 0:
            _lc = max(decision.long_view.confidence, 0.1)
            weighted_sum += l_score * long_w * _lc
            total_weight += long_w * _lc
        if m_score != 0:
            _mc = max(decision.mid_view.confidence, 0.1)
            weighted_sum += m_score * mid_w * _mc
            total_weight += mid_w * _mc
        if s_score != 0:
            _sc = max(decision.short_view.confidence, 0.1)
            weighted_sum += s_score * short_w * _sc
            total_weight += short_w * _sc
        normalized = weighted_sum / total_weight if total_weight > 0 else 0.0

        # 三周期完全一致时给予额外共振加成
        if l_score == m_score == s_score and l_score != 0:
            normalized = normalized * 1.2
            normalized = max(-1.0, min(1.0, normalized))

        if normalized > 0.3:
            decision.allowed_direction = "long_only"
            decision.position_multiplier = min(abs(normalized), 1.0)
            decision.coordination_note = f"加权共识偏多({normalized:+.2f})"
        elif normalized < -0.3:
            decision.allowed_direction = "short_only"
            decision.position_multiplier = min(abs(normalized), 1.0)
            decision.coordination_note = f"加权共识偏空({normalized:+.2f})"
        elif abs(normalized) > 0.1:
            decision.allowed_direction = "both"
            decision.position_multiplier = max(0.3, abs(normalized))
            direction_hint = "偏多" if normalized > 0 else "偏空"
            decision.coordination_note = f"弱共识{direction_hint}({normalized:+.2f})"
        else:
            decision.allowed_direction = "both"
            decision.position_multiplier = 0.3
            decision.coordination_note = f"方向不明({normalized:+.2f})，轻仓观望"

        # P1-7: 应用市场状态仓位缩放 (Crash→0, Ranging→0.25, TrendingUp→1.3)
        if decision.position_scale != 1.0:
            decision.position_multiplier *= decision.position_scale
            decision.coordination_note += (
                f" × regime_scale={decision.position_scale:.1f}"
            )

    # ════════════════════════ 事件覆盖 ════════════════════════

    def _check_event_overrides(self, decision: OrchestratorDecision, snapshot):
        if not snapshot:
            return

        # 检查新闻紧急事件（优先该 symbol 专属新闻）
        _news_list: List[Dict] = []
        if hasattr(snapshot, "news_by_symbol") and getattr(snapshot, "news_by_symbol", None):
            _news_list = list(snapshot.news_by_symbol.get(decision.symbol, []) or [])
        if not _news_list and hasattr(snapshot, "news_signals"):
            _sym_u = decision.symbol.upper()
            _news_list = [
                n for n in (snapshot.news_signals or [])
                if not n.get("symbol") or str(n.get("symbol", "")).upper() == _sym_u
            ]
        for news in _news_list:
                strength = news.get("strength", 1) or 1
                category = news.get("category", "") or ""
                direction = news.get("direction", 0) or 0

                # 2026-06-18: 新闻是交易信号，不是刹车。负面新闻 = 做空机会，正面新闻 = 做多机会。
                # 原"冻结"逻辑（freeze 整个 symbol 多空都不让开）完全反了——最该顺势开空时被绑住。
                # 现：新闻驱动 bias/final_side 方向（负面→偏空，正面→偏多），作为开单信号注入决策。
                # 触发阈值 4→7（只有高强度新闻才触发，中等新闻交给 AI 自行判断）。
                if strength >= 7 and abs(direction) > 0.5:
                    title = news.get("title", "")[:80]
                    news_hash = hash(title)
                    if news_hash in self._triggered_news_hashes:
                        continue
                    affected = news.get("symbols") or []
                    if affected and decision.symbol not in affected:
                        continue

                    if direction < -0.5:
                        # 负面新闻 → 偏空（做空信号）
                        decision.event_note = f"高强度负面新闻(strength={strength}): {title[:50]} → 偏空/做空信号"
                        # 直接驱动短期 view 偏空（这是 AI 决策的重要输入）
                        decision.short_view.bias = "bearish"
                        decision.short_view.confidence = max(decision.short_view.confidence, min(0.85, strength / 10.0))
                        decision.short_view.details += f" | 负面新闻做空信号(str={strength})"
                        logger.info(f"[MTOrchestrator] {decision.symbol} 负面新闻做空信号(str={strength}): {title[:50]}")
                    elif direction > 0.5:
                        # 正面新闻 → 偏多（做多信号）
                        decision.event_note = f"高强度正面新闻(strength={strength}): {title[:50]} → 偏多/做多信号"
                        decision.short_view.bias = "bullish"
                        decision.short_view.confidence = max(decision.short_view.confidence, min(0.85, strength / 10.0))
                        decision.short_view.details += f" | 正面新闻做多信号(str={strength})"
                        logger.info(f"[MTOrchestrator] {decision.symbol} 正面新闻做多信号(str={strength}): {title[:50]}")

                    self._triggered_news_hashes.add(news_hash)
                    if len(self._triggered_news_hashes) > 200:
                        self._triggered_news_hashes = set(list(self._triggered_news_hashes)[-100:])
                    break

        # 检查情绪极端值
        if decision.sentiment_zone == "extreme_fear" and not decision.event_override:
            decision.event_override = EVENT_OVERRIDE_RULES.get("extreme_fear")
            decision.event_note = "情绪极度恐惧，逆向试探"
        elif decision.sentiment_zone == "extreme_greed" and not decision.event_override:
            decision.event_override = EVENT_OVERRIDE_RULES.get("extreme_greed")
            decision.event_note = "情绪极度贪婪，收紧止盈"

        # 检查鲸鱼大额异动
        if hasattr(snapshot, 'whale_signals'):
            whale = snapshot.whale_signals.get(decision.symbol, {})
            whale_dir = whale.get("direction", 0)
            whale_usd = whale.get("total_usd", 0)
            if whale_usd > 50_000_000 and abs(whale_dir) > 0.5 and not decision.event_override:
                if whale_dir < -0.5:
                    decision.event_override = EVENT_OVERRIDE_RULES.get("whale_massive_sell")
                    decision.event_note = f"鲸鱼大额卖出 ${whale_usd:,.0f}"
                else:
                    decision.event_override = EVENT_OVERRIDE_RULES.get("whale_massive_buy")
                    decision.event_note = f"鲸鱼大额买入 ${whale_usd:,.0f}"

    # ════════════════════════ 最终决策 ════════════════════════

    def _cycle_prob_arbitration(self, decision: OrchestratorDecision, snapshot):
        """周期方向概率引擎的冲突仲裁分量（校准加权，弱信号自动近乎无影响）。

        返回 (score, active, note)：
          - score: 校准加权的方向净分 ∈ 约 [-1,1]，>0 偏多、<0 偏空；
                   = Σ_tier (P涨-P跌)_tier × 校准质量_tier × tier权重 / Σ(校准×权重)。
          - active: 是否有 tier 的校准质量达到最低阈值（否则视为不可信，不参与仲裁）。
        设计要点：当前加密方向校准质量普遍偏低（≈0.05），score 天然被压得很小，
        因此这只是"锦上添花"的微调，不会颠覆既有加权投票；随数据积累校准变好后才显著。
        """
        try:
            from backend.services.cycle_direction_probability import (
                cycle_probability_engine, extract_tier_features_from_snapshot,
            )
            from backend.config.settings import CYCLE_PROB_GATE_MIN_CALIBRATION
        except Exception:
            return 0.0, False, ""
        ind = {}
        try:
            if snapshot and hasattr(snapshot, "indicators"):
                ind = snapshot.indicators.get(decision.symbol, {}) or {}
        except Exception:
            ind = {}
        if not ind:
            return 0.0, False, ""

        _w = {"long": self._params.get("finalize_long_weight", 0.30),
              "mid": self._params.get("finalize_mid_weight", 0.40),
              "short": self._params.get("finalize_short_weight", 0.30)}
        num = 0.0
        den = 0.0
        active = False
        parts = []
        for tier in ("long", "mid", "short"):
            try:
                feats = extract_tier_features_from_snapshot(ind, tier)
                res = cycle_probability_engine.estimate(tier, feats)
            except Exception:
                continue
            if not res.available:
                continue
            q = float(res.calibration_quality or 0.0)
            if q >= CYCLE_PROB_GATE_MIN_CALIBRATION:
                active = True
            lean = float(res.prob_up - res.prob_down)  # >0 偏多
            weight = q * _w.get(tier, 0.3)
            num += lean * weight
            den += weight
            parts.append(f"{tier}:{res.direction}(q{q:.2f})")
        score = (num / den) if den > 1e-9 else 0.0
        note = "概率仲裁 " + " ".join(parts) if parts else ""
        return score, active, note

    def _finalize(self, decision: OrchestratorDecision, snapshot):
        """综合所有分析，输出最终交易建议"""

        # 基础风险预算
        base_budget = 0.5
        if snapshot and hasattr(snapshot, 'strategy'):
            base_budget = getattr(snapshot.strategy, 'max_position_size', 0.25)

        decision.risk_budget_pct = base_budget

        # 情绪调整
        sent_adj = 1.0
        if decision.sentiment_zone == "extreme_fear":
            sent_adj = 0.5
        elif decision.sentiment_zone == "fear":
            sent_adj = 0.7
        elif decision.sentiment_zone == "greed":
            sent_adj = 0.8
        elif decision.sentiment_zone == "extreme_greed":
            sent_adj = 0.6

        # 处理事件覆盖
        if decision.event_override:
            action = decision.event_override.get("action", "")
            if action in ("close_all_long", "close_all_short"):
                decision.final_action = action
                decision.final_position_pct = 0
                decision.reasoning = f"事件覆盖: {decision.event_note}"
                return
            elif action == "reduce_to_30pct":
                decision.position_multiplier = min(decision.position_multiplier, 0.3)
            elif action == "tighten_sl_50pct":
                decision.final_sl_pct *= 0.5
            elif action == "contrarian_small_long":
                decision.allowed_direction = "long_only"
                decision.position_multiplier = 0.1
            elif action == "tighten_tp":
                decision.final_tp_pct *= 0.7

        # 判定最终方向（综合三周期，方向矛盾时不开仓）
        final_side = ""

        # 收集三周期方向
        biases = []
        for v in [decision.long_view, decision.mid_view, decision.short_view]:
            if v.bias in ("bullish", "bearish"):
                biases.append(v.bias)

        bullish_count = sum(1 for b in biases if b == "bullish")
        bearish_count = sum(1 for b in biases if b == "bearish")

        # F4-fix: 方向矛盾时不再一律 wait —— 按置信度加权多数决定方向
        # 子仓位系统允许不同周期持有不同仓位，矛盾只是降低仓位而非拒绝入场
        direction_conflict = bullish_count > 0 and bearish_count > 0

        logger.info(
            f"[MTOrchestrator._finalize] 方向检测: "
            f"biases={biases} bull={bullish_count} bear={bearish_count} "
            f"conflict={direction_conflict}"
        )

        if direction_conflict:
            # 计算置信度加权方向分数（默认权重与_coordinate/DEFAULT_PARAMS一致）
            _w_long = self._params.get("finalize_long_weight", 0.30)
            _w_mid = self._params.get("finalize_mid_weight", 0.40)
            _w_short = self._params.get("finalize_short_weight", 0.30)
            _bias_val = lambda b: 1 if b == "bullish" else (-1 if b == "bearish" else 0)
            _weighted_dir = (
                _bias_val(decision.long_view.bias) * decision.long_view.confidence * _w_long
                + _bias_val(decision.mid_view.bias) * decision.mid_view.confidence * _w_mid
                + _bias_val(decision.short_view.bias) * decision.short_view.confidence * _w_short
            )
            # 2/3 多数派规则：3 个周期中有 2 个同向即视为明确多数，允许入场
            top_bias = "bullish" if bullish_count >= bearish_count else "bearish"
            top_count = max(bullish_count, bearish_count)
            has_clear_majority = top_count >= 2  # 2/3 或以上同向

            # ── 概率仲裁分量（校准加权）：冲突时作为方向证据的补充仲裁 ──
            _prob_score, _prob_active, _prob_note = self._cycle_prob_arbitration(decision, snapshot)
            _top_val = 1.0 if top_bias == "bullish" else -1.0
            _prob_agreement = _prob_score * _top_val  # >0 概率支持多数方向，<0 反对

            if abs(_weighted_dir) < 0.05 and not has_clear_majority:
                # 真正势均力敌 → wait
                decision.final_action = "wait"
                decision.reasoning = (
                    f"多周期方向拉锯(加权={_weighted_dir:+.2f}, {bullish_count}多/{bearish_count}空) — "
                    f"长={decision.long_view.bias}/中={decision.mid_view.bias}/"
                    f"短={decision.short_view.bias}，暂不入场"
                )
                return

            # 有明确多数方向 → 允许入场
            # - 2/3 多数派：仓位 ×0.7（较分歧小）
            # - 其他分歧：仓位 ×0.5
            _reduce_mult = 0.7 if has_clear_majority else 0.5
            # 概率仲裁只在"有可信校准(active)"时才微调，且是硬约束(H1-H5/constraint)之下的软层：
            #   - 概率明确反对多数方向 → 额外缩仓（≥0.6 倍）；极端反对(<-0.15)直接 wait
            #   - 概率明确支持 → 允许小幅放宽（≤1.0，不放大越界）
            _prob_tag = ""
            if _prob_active:
                if _prob_agreement <= -0.15:
                    decision.final_action = "wait"
                    decision.reasoning = (
                        f"多周期分歧且{_prob_note}明显反对多数方向"
                        f"(agreement={_prob_agreement:+.2f})，暂不入场"
                    )
                    return
                if _prob_agreement < -0.03:
                    _reduce_mult *= 0.6
                    _prob_tag = f"，{_prob_note}反对→再缩仓×0.6"
                elif _prob_agreement > 0.03:
                    _reduce_mult = min(1.0, _reduce_mult * 1.1)
                    _prob_tag = f"，{_prob_note}支持→轻放宽"
            decision.position_multiplier *= _reduce_mult
            decision.reasoning = (
                f"多周期{('2/3多数派' if has_clear_majority else '有分歧')}"
                f"(加权={_weighted_dir:+.2f}, {bullish_count}多/{bearish_count}空) — "
                f"长={decision.long_view.bias}/中={decision.mid_view.bias}/"
                f"短={decision.short_view.bias}，按多数方向入场，仓位×{_reduce_mult:.2f}{_prob_tag}"
            )

        # ── P1-2: 方向选择改为加权投票制（替代旧的短期优先级联） ──
        # 使用三周期 bias × confidence × weight 综合评分，防止短期噪音主导
        _w_long = self._params.get("finalize_long_weight", 0.30)
        _w_mid = self._params.get("finalize_mid_weight", 0.40)
        _w_short = self._params.get("finalize_short_weight", 0.30)
        _bias_val = lambda b: 1 if b == "bullish" else (-1 if b == "bearish" else 0)

        # 修复（2026-06-24）：原公式让 short_view 的均值回归信号（下跌中超卖反弹=看多）
        # 轻易压倒 long_view 的趋势看空，导致在持续下跌中反复做多全亏。
        # 现加入"趋势保护"：当 long_view 是趋势方向（看空/看多）时，
        # 给 long_view 额外加权（×1.5），避免短线噪声覆盖中线趋势。
        # 这是"趋势为王，短线确认"的经典原则。
        _trend_protect = 1.0
        if decision.long_view.bias in ("bullish", "bearish") and decision.long_view.confidence > 0.5:
            _trend_protect = 1.5  # 长线趋势明确时加权 50%

        _weighted_dir = (
            _bias_val(decision.long_view.bias) * decision.long_view.confidence * _w_long * _trend_protect
            + _bias_val(decision.mid_view.bias) * decision.mid_view.confidence * _w_mid
            + _bias_val(decision.short_view.bias) * decision.short_view.confidence * _w_short
        )

        # ── P1-3: 方向一致性门控 — 少于2个周期同向时提高置信度门槛 ──
        _active_views = [v for v in [decision.long_view, decision.mid_view, decision.short_view]
                         if v.bias in ("bullish", "bearish")]
        _active_count = len(_active_views)
        _same_dir = (bullish_count >= 2 or bearish_count >= 2)  # ≥2 周期同向

        if _active_count == 0:
            # 全 neutral → wait
            decision.final_action = "wait"
            decision.reasoning = f"{decision.coordination_note}，三周期均无明确方向"
            return

        if _active_count == 1 and not _same_dir:
            # P1-3: 仅1个周期有方向 → 需要更高置信度才允许
            _sole_view = _active_views[0]
            _min_sole_conf = 0.45 if _sole_view.timeframe == "long" else 0.50
            if _sole_view.confidence < _min_sole_conf:
                decision.final_action = "wait"
                decision.reasoning = (
                    f"{decision.coordination_note}，仅{_sole_view.timeframe}周期有方向"
                    f"(置信度{_sole_view.confidence:.0%}<{_min_sole_conf:.0%})，证据不足"
                )
                return

        # 加权投票决定方向
        if _weighted_dir > 0.02:
            final_side = "long"
        elif _weighted_dir < -0.02:
            final_side = "short"
        else:
            final_side = ""

        # 方向限制
        if decision.allowed_direction == "long_only" and final_side == "short":
            final_side = ""
        elif decision.allowed_direction == "short_only" and final_side == "long":
            final_side = ""

        if not final_side:
            decision.final_action = "wait"
            decision.reasoning = (
                f"{decision.coordination_note}，加权方向={_weighted_dir:+.3f}不足"
                f"(长={decision.long_view.bias}/中={decision.mid_view.bias}/"
                f"短={decision.short_view.bias})"
            )
            return

        # ── 币圈全局清算簇熔断（最后一道防线）──
        # 若当前 symbol 存在 high severity 清算簇磁吸且与 final_side 反向 → wait。
        # 短/中/长三层都可能被骗（数据延迟/LLM 忽略），这层用原始清算数据兜底，
        # 防止在级联清算中逆势开仓。详见 crypto_alpha_signals.py。
        #
        # 修复（2026-06-24）：原代码拦 long 后直接 wait，浪费了顺势做空机会。
        # 现改为：拦 long 后若清算磁吸方向是 short（下方磁吸=价格继续跌），
        # 则顺势转为 short，而非放弃。这让系统在崩盘中能捕捉做空机会。
        try:
            from backend.services.crypto_alpha_signals import crypto_alpha
            _lm = crypto_alpha.liquidation_magnet(decision.symbol)
            if _lm.available and _lm.severity == "high" and _lm.direction != "neutral":
                _final_dir = "long" if final_side == "long" else "short"
                _opp = "short" if _final_dir == "long" else "long"
                if _lm.direction == _opp:
                    # 原方向被清算磁吸反向拦截
                    if _lm.direction == "short":
                        # 下方强磁吸（多头被清算，价格继续跌）→ 顺势转 short
                        final_side = "short"
                        decision.final_side = "short"
                        decision.reasoning = (
                            f"🎯 清算簇顺势做空: {_lm.note}，原做多被拦，"
                            f"转为顺势做空(空头级联)"
                        )
                        logger.info(
                            "[MTOrchestrator] %s 清算簇熔断: long→short 顺势转换: %s",
                            decision.symbol, _lm.note,
                        )
                    elif _lm.direction == "long":
                        # 上方强磁吸（空头被清算，价格继续涨）→ 顺势转 long
                        final_side = "long"
                        decision.final_side = "long"
                        decision.reasoning = (
                            f"🎯 清算簇顺势做多: {_lm.note}，原做空被拦，"
                            f"转为顺势做多(多头级联)"
                        )
                        logger.info(
                            "[MTOrchestrator] %s 清算簇熔断: short→long 顺势转换: %s",
                            decision.symbol, _lm.note,
                        )
                    else:
                        # 磁吸方向不明确 → wait
                        decision.final_action = "wait"
                        decision.final_side = ""
                        decision.reasoning = (
                            f"⚠️ 币圈清算簇熔断: {_lm.note}，与{final_side}方向反向"
                            f"(severity=high)，全局拦截逆势开仓"
                        )
                        return
        except Exception as _liq_err:
            logger.debug(f"[MTOrchestrator] 清算簇熔断检查跳过: {_liq_err}")

        # P1-3: 少于2周期同向时标记为弱共识，仓位打折
        if not _same_dir and _active_count == 2:
            decision.position_multiplier *= 0.75
            _dir_note = "(仅1-1分歧，仓位×0.75)"
        elif not _same_dir and _active_count >= 2:
            _dir_note = ""
        elif _same_dir and _active_count == 3 and not direction_conflict:
            _dir_note = "(3周期一致)"
        else:
            _dir_note = ""

        p = self._params
        weighted_conf = (
            decision.long_view.confidence * p["finalize_long_weight"]
            + decision.mid_view.confidence * p["finalize_mid_weight"]
            + decision.short_view.confidence * p["finalize_short_weight"]
        )
        active_confs = [v.confidence for v in
                        [decision.long_view, decision.mid_view, decision.short_view]
                        if v.bias != "neutral" and v.confidence > 0]
        max_active_conf = max(active_confs) if active_confs else 0
        avg_conf = max_active_conf * p["finalize_max_active_ratio"] + weighted_conf * (1 - p["finalize_max_active_ratio"])

        min_conf = p["finalize_min_conf"]
        # 编排器 enter 与 AI 门槛对齐：ranging 时略降低综合置信度要求
        _regime = (decision.regime or "").lower()
        if "ranging" in _regime or "range" in _regime:
            min_conf = min(min_conf, 0.32)
        if avg_conf < min_conf:
            decision.final_action = "wait"
            decision.reasoning = f"综合置信度不足 ({avg_conf:.0%} < {min_conf:.0%})"
            return

        # 计算仓位
        position_pct = base_budget * decision.position_multiplier * sent_adj * avg_conf
        position_pct = max(0.02, min(0.5, position_pct))

        # AI 策略杠杆：由三周期综合置信度动态决定（5~20x），不再写死 15x
        leverage = max(5, min(20, int(round(5 + avg_conf * 15))))

        # SL/TP 根据方向微调
        sl_pct = decision.final_sl_pct
        tp_pct = decision.final_tp_pct

        # ── bias 稳定冷却：防止震荡市频繁翻转 ──
        prev_side = self._last_side.get(decision.symbol, "")
        if prev_side and prev_side != final_side:
            now = time.time()
            pending = self._pending_flip.get(decision.symbol, "")
            pending_ts = self._pending_flip_ts.get(decision.symbol, 0.0)

            # ── 时间衰减：超过 2 小时的待确认自动重置 ──
            if pending and (now - pending_ts) > 7200:
                self._side_confirm_count[decision.symbol] = 0
                self._pending_flip[decision.symbol] = ""
                self._pending_flip_ts[decision.symbol] = 0.0
                logger.info(
                    f"[MTOrchestrator] {decision.symbol} bias翻转待确认超时重置"
                )

            # ── 新方向变化：重置计数器 ──
            if pending and pending != final_side:
                # 待确认的方向也变了 → 重置（避免浪费）
                self._side_confirm_count[decision.symbol] = 0

            confirm_count = self._side_confirm_count.get(decision.symbol, 0) + 1
            self._side_confirm_count[decision.symbol] = confirm_count
            self._pending_flip[decision.symbol] = final_side
            if confirm_count == 1:
                self._pending_flip_ts[decision.symbol] = now

            # ── 自适应确认次数 ──
            required = 3
            # 强趋势中翻转需更多确认
            adx_4h = 0.0
            try:
                if snapshot and hasattr(snapshot, 'indicators'):
                    adx_4h = snapshot.indicators.get(decision.symbol, {}).get("adx_4h", 0.0)
            except Exception:
                pass
            if adx_4h > 30:
                required = 5

            # 高置信度可减少确认次数（强信号快速响应）
            if avg_conf >= 0.55:
                required = max(2, required - 1)

            if confirm_count < required:
                decision.final_action = "wait"
                decision.reasoning = (
                    f"bias翻转冷却中({confirm_count}/{required}): "
                    f"{prev_side}→{final_side}，需连续{required}次确认"
                )
                logger.info(
                    f"[MTOrchestrator] {decision.symbol} bias翻转冷却 "
                    f"{prev_side}→{final_side} ({confirm_count}/{required})"
                )
                return
            else:
                # 确认次数达标 → 允许翻转，清理状态
                logger.info(
                    f"[MTOrchestrator] {decision.symbol} bias翻转确认通过 "
                    f"{prev_side}→{final_side} ({confirm_count}/{required}次)"
                )
        else:
            self._side_confirm_count[decision.symbol] = 0
            self._pending_flip[decision.symbol] = ""
            self._pending_flip_ts[decision.symbol] = 0.0

        self._last_side[decision.symbol] = final_side

        decision.final_action = "enter"
        decision.final_side = final_side
        decision.final_position_pct = round(position_pct, 3)
        decision.final_leverage = leverage
        decision.final_sl_pct = round(sl_pct, 4)
        decision.final_tp_pct = round(tp_pct, 4)
        decision.reasoning = (
            f"{decision.coordination_note} | "
            f"置信={avg_conf:.0%} 情绪={decision.sentiment_index:.0f}({decision.sentiment_zone}) | "
            f"{decision.event_note}" if decision.event_note else decision.coordination_note
        )

        # 推荐交易性质：基于哪个周期驱动了最终决策
        decision.recommended_nature = self._infer_trade_nature(decision)

    # ════════════════════════ 推荐交易性质 ════════════════════════

    @staticmethod
    def _infer_trade_nature(decision: OrchestratorDecision) -> str:
        """基于 L/M/S 分析结果推荐交易性质（用于策略创建时分配 tier）

        五档分类（治理后）：
          scalp        ：极短线 / 高频 （分钟级，低置信也可承接）
          intraday     ：日内确认方向  （短期高置信，或与中期共振）
          swing        ：波段（中期主导）
          trend_follow ：趋势跟随（长期偏向但非极强）
          position    ：长期持有（长期极强置信 且 无反向）

        阈值调整说明：
          - scalp 不再被 0.60 高置信门槛"升级"吞掉：允许低置信短线独立存在
          - position 门槛从 0.70 降至 0.55：长期中-高置信即可落地
          - 加入"无反向"校验：只有在 L 极强 + 中短无反向时才升 position
        """
        lv = decision.long_view
        mv = decision.mid_view
        sv = decision.short_view

        active_confs = []
        if sv.bias != "neutral" and sv.confidence >= 0.25:
            active_confs.append((sv.confidence, sv.bias, "scalp"))
        if mv.bias != "neutral" and mv.confidence >= 0.25:
            active_confs.append((mv.confidence, mv.bias, "swing"))
        if lv.bias != "neutral" and lv.confidence >= 0.30:
            active_confs.append((lv.confidence, lv.bias, "position"))

        if not active_confs:
            return "swing"

        active_confs.sort(key=lambda x: x[0], reverse=True)
        top_conf, top_bias, nature = active_confs[0]

        same_bias_count = sum(1 for c, b, _ in active_confs if b == top_bias)
        l_opposed = lv.bias in ("bullish", "bearish") and lv.bias != top_bias
        s_opposed = sv.bias in ("bullish", "bearish") and sv.bias != top_bias

        # 多周期同向共振 → 升级策略类型
        if same_bias_count >= 2 and top_conf >= 0.40:
            if top_bias == sv.bias:
                # 短期主导 + 多周期共振：
                # - 超高置信(>=0.75)才升为 intraday
                # - 中低置信保留 scalp 属性（允许超短线独立存在）
                if sv.confidence >= 0.75 and not l_opposed:
                    return "intraday"
                return "scalp"
            elif top_bias == mv.bias:
                return "swing"
            else:
                # 长期主导 + 共振：极强置信且无反向 → position，否则 trend_follow
                if lv.confidence >= 0.55 and not s_opposed:
                    return "position"
                return "trend_follow"

        # 单周期主导
        if nature == "scalp":
            # 短期单独主导：只有非常强的短期信号才升 intraday；否则保留 scalp
            if sv.confidence >= 0.75:
                return "intraday"
            return "scalp"
        elif nature == "swing":
            return "swing"
        else:
            # 长期单独主导：只有极强置信 + 无反向才 position，否则 trend_follow
            if lv.confidence >= 0.55 and not s_opposed:
                return "position"
            return "trend_follow"

    # ════════════════════════ 智能槽位推荐 ════════════════════════

    def _recommend_slots(self, decision: OrchestratorDecision):
        """根据三周期分析结果推荐应该激活的槽位。

        优化版：不再只输出单一 active 槽位，
        而是根据各周期的置信度独立判断是否激活对应槽位。
        同时确保不会只激活 mid，至少尝试覆盖 short 和 long。
        """
        long_v = decision.long_view
        mid_v = decision.mid_view
        short_v = decision.short_view

        # 获取各周期置信度门槛（降低门槛以激活更多槽位）
        short_thresh = self._params.get("short_conf_threshold", 0.20)
        mid_thresh = self._params.get("finalize_mid_fallback_conf", 0.20)
        long_thresh = self._params.get("finalize_long_fallback_conf", 0.20)

        recommended = []
        actions = {}
        reasoning = {}

        # 事件冻结时全部暂停
        if decision.event_override:
            action = decision.event_override.get("action", "")
            if action in ("close_all_long", "close_all_short", "reduce_to_30pct"):
                decision.recommended_slots = []
                decision.slot_actions = {"long": "pause", "mid": "pause", "short": "pause"}
                decision.slot_reasoning = {"long": f"紧急事件: {decision.event_note}", "mid": f"紧急事件: {decision.event_note}", "short": f"紧急事件: {decision.event_note}"}
                return

        # 长期槽位：长期信号明确时激活
        # 修复（2026-06-26）：原逻辑 long action 依赖 final_action=="enter"，
        # 但 M=neutral 时加权方向弱 → final_action=wait → 长线永远 pause → TrendAgent 不触发。
        # 现改为长线独立触发：只要长线 view 有方向（bias≠neutral 且 conf≥阈值），
        # 就给 create 动作，让 TrendAgent 有机会独立分析。
        if long_v.bias != "neutral" and long_v.confidence >= long_thresh:
            recommended.append("long")
            actions["long"] = "create"  # 长线独立触发，不等 final_action
            reasoning["long"] = f"长期{long_v.bias}({long_v.confidence:.0%})[独立触发]"

        # 中期槽位：中期信号明确时激活
        try:
            from backend.config.settings import ORCH_MID_INDEPENDENT_TRIGGER
            _mid_independent = ORCH_MID_INDEPENDENT_TRIGGER
        except Exception:
            _mid_independent = False
        if mid_v.bias != "neutral" and mid_v.confidence >= mid_thresh:
            recommended.append("mid")
            if _mid_independent:
                actions["mid"] = "create"
                reasoning["mid"] = f"中期{mid_v.bias}({mid_v.confidence:.0%})[独立触发]"
            else:
                actions["mid"] = "create" if decision.final_action == "enter" else "pause"
                reasoning["mid"] = f"中期{mid_v.bias}({mid_v.confidence:.0%})"

        # 短期槽位：短期信号明确时激活
        if short_v.bias != "neutral" and short_v.confidence >= short_thresh:
            recommended.append("short")
            actions["short"] = "create" if decision.final_action == "enter" else "pause"
            reasoning["short"] = f"短期{short_v.bias}({short_v.confidence:.0%})"

        # 2026-07-06 整改（审查 3 #18）：此前"只激活了 mid 就自动跟随创建同向
        # short+long 槽位"的逻辑已删除——short/long 各自代表完全不同的交易周期
        # 和仓位管理规则（止损/持仓时长/仓位上限均不同），仅因为 mid 有信号就
        # 让 short/long 跟着"被动"开仓，等价于用中期证据去承担长/短线的仓位
        # 风险，一旦 mid 判断错误会同时打三个方向的仓。终态：long/short 槽位
        # 只能由上面各自独立的 long_v/short_v 置信度门槛判断触发（见上方两段），
        # mid 信号强并不能替代 long_view/short_view 自己的分析结论。

        # 如果没有任何槽位被激活，但有交易信号，则激活中期槽位作为默认
        if not recommended and decision.final_action == "enter" and decision.final_side:
            recommended = ["mid"]
            actions["mid"] = "create"
            reasoning["mid"] = "默认激活中期槽位"

        # 如果没有任何槽位被激活，暂停所有槽位
        if not recommended:
            actions = {k: "pause" for k in ["long", "mid", "short"]}
            reasoning = {
                "long": f"长期{long_v.bias}({long_v.confidence:.0%})",
                "mid": f"中期{mid_v.bias}({mid_v.confidence:.0%})",
                "short": f"短期{short_v.bias}({short_v.confidence:.0%})"
            }

        decision.recommended_slots = recommended
        decision.slot_actions = actions
        decision.slot_reasoning = reasoning
    
    # ════════════════════════ 工具 ════════════════════════

    def _normalize_bias(self, bias: str) -> str:
        b = bias.lower()
        if b in ("long", "bullish", "bull"):
            return "bullish"
        if b in ("short", "bearish", "bear"):
            return "bearish"
        return "neutral"

    def _apply_frequency_constraints(
        self,
        decision: OrchestratorDecision,
        *,
        symbol: str,
    ) -> OrchestratorDecision:
        """
        P2.6 多频率约束链 — 5条硬约束 + 3条软约束

        硬约束（违反→直接修改决策）：
        1. 4h方向 ≠ 15m方向 → 降低仓位50%
        2. 4h强烈信号(confidence>0.7) → 禁止15m反向开仓
        3. 15m过热(RSI>80/<20) → 禁止追涨杀跌
        4. 1h/4h波动率不匹配 → 收紧止损
        5. 跨周期信号冲突数 ≥2 → 降级为 wait

        软约束（违反→降低confidence+记录警告）：
        1. 15m成交量衰竭(vol<20日均量50%) → confidence*0.7
        2. 1h/4h方向一致但15m背离 → confidence*0.8
        3. 长周期刚完成一次较大波动(>3σ) → 短期增加冷却

        冲突升级+自动解冻机制：
        - 连续3次被同一约束拒绝 → 冻结该symbol 30min
        - 冻结期满+新信号方向与约束一致 → 自动解冻
        """
        from datetime import datetime, timezone
        import numpy as np

        conflicts = []
        d = decision

        # ─── 硬约束 ───

        # H1: 4h方向 ≠ 15m方向 → 降低仓位50%
        # 2026-07-06 修正（审查 3 #5）：注释写"4h vs 15m"，但此前实现比较的是
        # d.long_view（本文件里代表 1d/1w 长周期，见 _analyze_long_term）与
        # d.short_view（5m/15m），根本没有比较真正的 4h。本文件的 d.mid_view
        # 才是真正代表 4h 的视图（_analyze_mid_term 明确"中期优先使用 4h 动量，
        # 真正的中周期"）。改为比较 mid_view(4h) 与 short_view(15m)。
        if (
            d.mid_view.bias != "neutral"
            and d.short_view.bias != "neutral"
            and d.mid_view.bias != d.short_view.bias
        ):
            orig_pct = d.final_position_pct
            d.final_position_pct = max(0.02, d.final_position_pct * 0.5)
            conflicts.append({
                "constraint": "H1_4h_vs_15m_conflict",
                "severity": "hard",
                "action": f"position {orig_pct:.0%}→{d.final_position_pct:.0%}",
            })

        # H2: 4h强烈信号 → 禁止15m反向
        # 同 H1，改为以 mid_view（4h）为准，而非 long_view（1d/1w）。
        if d.mid_view.confidence > 0.7 and d.short_view.bias not in (
            d.mid_view.bias, "neutral"
        ):
            if d.short_view.bias != "neutral":
                d.short_view = TimeframeView(
                    timeframe="short",
                    bias=d.mid_view.bias,
                    confidence=d.short_view.confidence * 0.5,
                    details=f"[H2] 4h强信号覆盖: {d.mid_view.bias}",
                )
                conflicts.append({
                    "constraint": "H2_4h_strong_override_15m",
                    "severity": "hard",
                    "action": f"15m→{d.mid_view.bias}",
                })

        # H3: 15m RSI过热 → 禁止追涨杀跌
        try:
            from backend.services.unified_data_pool import unified_data_pool
            snap = unified_data_pool.get_snapshot(max_age=30)
            if snap:
                indicators = snap.indicators.get(symbol, {}) if hasattr(snap, 'indicators') else {}
                rsi = float(indicators.get("rsi", 50) or 50)
                if rsi > 80 and d.short_view.bias == "bullish":
                    d.short_view = TimeframeView(
                        timeframe="short", bias="neutral", confidence=0.2,
                        details=f"[H3] RSI={rsi:.0f}过热, 禁止追多",
                    )
                    conflicts.append({"constraint": "H3_rsi_overbought", "severity": "hard"})
                elif rsi < 20 and d.short_view.bias == "bearish":
                    d.short_view = TimeframeView(
                        timeframe="short", bias="neutral", confidence=0.2,
                        details=f"[H3] RSI={rsi:.0f}超卖, 禁止追空",
                    )
                    conflicts.append({"constraint": "H3_rsi_oversold", "severity": "hard"})
        except Exception:
            pass

        # H4: 波动率不匹配 → 收紧止损
        if d.long_view.bias != "neutral" and d.short_view.bias != "neutral":
            # 简化版：如果4h ATR > 2x 1h ATR，缩紧短周期止损
            try:
                from backend.services.unified_data_pool import unified_data_pool
                snap = unified_data_pool.get_snapshot(max_age=30)
                if snap:
                    ind = snap.indicators.get(symbol, {}) if hasattr(snap, 'indicators') else {}
                    atr_val = float(ind.get("atr", 0) or 0)
                    cur_price = float(snap.price if hasattr(snap, 'price') else (snap.get('price', 0) if isinstance(snap, dict) else 0))
                    if atr_val > 0 and cur_price > 0 and atr_val / cur_price > 0.05:
                        d.final_sl_pct = max(0.005, d.final_sl_pct * 0.7)
                        conflicts.append({
                            "constraint": "H4_high_volatility",
                            "severity": "hard",
                            "action": f"SL收紧: ATR/Price={atr_val/cur_price:.3f}",
                        })
            except Exception:
                pass

        # H5: 跨周期冲突 ≥2 → 降级为wait
        if len([c for c in conflicts if c["severity"] == "hard"]) >= 2:
            d.final_action = "wait"
            d.final_side = "none"
            d.final_position_pct = 0.0
            conflicts.append({
                "constraint": "H5_multi_conflict_escalation",
                "severity": "hard",
                "action": "强制降级→wait",
            })
            # 记录冲突升级
            conflict_key = f"conflict_{symbol}"
            self._conflict_count[conflict_key] = self._conflict_count.get(conflict_key, 0) + 1
            if self._conflict_count[conflict_key] >= 3:
                self._freeze_until[symbol] = time.time() + 1800  # 30分钟
                self._freeze_reason[symbol] = "连续3次跨周期冲突"
                logger.warning(f"[MTOrch] 冲突升级: {symbol} 冻结30min (H5)")

        # ─── 软约束 ───

        # S1: 成交量衰竭
        try:
            from backend.services.unified_data_pool import unified_data_pool
            klines = unified_data_pool.get_kline_series(symbol, interval="15m", limit=20)
            if klines and len(klines) >= 5:
                recent_vol = float(np.mean([float(k.volume or 0) for k in klines[-5:]]))
                all_vol = float(np.mean([float(k.volume or 0) for k in klines]))
                if all_vol > 0 and recent_vol < all_vol * 0.5:
                    d.final_confidence = getattr(d, 'final_confidence', d.short_view.confidence)
                    d.final_confidence *= 0.7
                    conflicts.append({
                        "constraint": "S1_volume_depletion",
                        "severity": "soft",
                        "action": f"confidence*0.7",
                    })
        except Exception:
            pass

        # S2: 1h/4h方向一致但15m背离
        if (
            d.long_view.bias == d.mid_view.bias
            and d.long_view.bias != "neutral"
            and d.short_view.bias not in (d.long_view.bias, "neutral")
        ):
            d.final_confidence = getattr(d, 'final_confidence', d.mid_view.confidence)
            d.final_confidence *= 0.8
            conflicts.append({
                "constraint": "S2_15m_divergence",
                "severity": "soft",
                "action": f"confidence*0.8: 1h/4h={d.long_view.bias}≠15m={d.short_view.bias}",
            })

        # S3: 长周期刚经历大波动 → 短期冷却
        if d.long_view.bias != "neutral" and d.long_view.confidence > 0.8:
            self._last_big_move_ts[symbol] = time.time()
        last_big = self._last_big_move_ts.get(symbol, 0)
        if time.time() - last_big < 600 and d.final_action in ("open_long", "open_short"):
            # 大波动10分钟内短周期减仓
            d.final_position_pct = max(0.01, d.final_position_pct * 0.6)
            conflicts.append({
                "constraint": "S3_recent_big_move_cooldown",
                "severity": "soft",
                "action": f"position*0.6: 距上次大波动{time.time()-last_big:.0f}s",
            })

        # ─── 自动解冻检查 ───
        if self._is_frozen(symbol):
            # 检查是否满足解冻条件：新信号方向与约束一致
            if (
                d.long_view.bias == d.mid_view.bias == d.short_view.bias
                and d.long_view.bias != "neutral"
                and len([c for c in conflicts if c["severity"] == "hard"]) == 0
            ):
                self._freeze_until.pop(symbol, None)
                self._freeze_reason.pop(symbol, None)
                self._conflict_count.pop(f"conflict_{symbol}", None)
                logger.info(f"[MTOrch] 自动解冻 {symbol}: 三周期信号一致")

        # ─── 输出冲突日志 ───
        if conflicts:
            summary = "; ".join(
                f"{c['constraint']}({c['severity']})" for c in conflicts
            )
            logger.info(f"[MTOrch] 频率约束链 {symbol}: {len(conflicts)}条 ({summary})")

        # 将冲突记录附到决策上
        if not hasattr(d, 'frequency_constraints'):
            d.frequency_constraints = []
        d.frequency_constraints.extend(conflicts)

        return d

    @property
    def _conflict_count(self) -> Dict[str, int]:
        if not hasattr(self, '_conflict_counter'):
            self._conflict_counter: Dict[str, int] = {}
        return self._conflict_counter

    @property
    def _last_big_move_ts(self) -> Dict[str, float]:
        if not hasattr(self, '_big_move_tracker'):
            self._big_move_tracker: Dict[str, float] = {}
        return self._big_move_tracker

    def _is_frozen(self, symbol: str) -> bool:
        until = self._freeze_until.get(symbol, 0)
        if until > time.time():
            return True
        if until > 0:
            del self._freeze_until[symbol]
            self._freeze_reason.pop(symbol, None)
        return False

    def _get_snapshot(self, symbol: str):
        try:
            from backend.services.unified_data_pool import unified_data_pool
            snap = unified_data_pool.get_snapshot(max_age=30)
            if snap is None:
                snap = unified_data_pool.capture_snapshot([symbol])
            return snap
        except Exception as e:
            logger.warning(f"[MTOrchestrator] 获取快照失败: {e}")
            return None

    def to_strategy_params(self, decision: OrchestratorDecision) -> Dict[str, Any]:
        """将编排器决策转换为策略参数（供 FullAutoTradingService 使用）"""
        return {
            "symbol": decision.symbol,
            "action": decision.final_action,
            "side": decision.final_side,
            "position_pct": decision.final_position_pct,
            "leverage": decision.final_leverage,
            "stop_loss_pct": decision.final_sl_pct,
            "take_profit_pct": decision.final_tp_pct,
            "risk_budget_pct": decision.risk_budget_pct,
            "reasoning": decision.reasoning,
            "sentiment_index": decision.sentiment_index,
            "sentiment_zone": decision.sentiment_zone,
            "long_bias": decision.long_view.bias,
            "long_confidence": decision.long_view.confidence,
            "mid_bias": decision.mid_view.bias,
            "mid_confidence": decision.mid_view.confidence,
            "short_bias": decision.short_view.bias,
            "short_confidence": decision.short_view.confidence,
            "allowed_direction": decision.allowed_direction,
            "event_override": decision.event_note or None,
            # 智能槽位推荐
            "recommended_slots": decision.recommended_slots,
            "slot_actions": decision.slot_actions,
            "slot_reasoning": decision.slot_reasoning,
            # 子仓位身份标签
            "recommended_nature": getattr(decision, "recommended_nature", "swing"),
        }


# 全局单例
mt_orchestrator = MultiTimeframeOrchestrator()
