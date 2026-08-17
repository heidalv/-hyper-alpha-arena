"""
情报信号融合引擎 — 将多源情报数据融合为交易方向信号

核心职责：
1. 采集各情报源数据（资金费率/OI/清算/鲸鱼/新闻/恐贪指数）
2. 对每个数据源独立研判方向（regime classification）
3. 按权重汇流计算最终交易方向 + 置信度
4. 输出结构化信号供 AI Prompt 和前端仪表盘使用

信号研判框架（行业标准）：
- 资金费率状态：极端正=多头拥挤→做空 / 极端负=空头拥挤→做多
- OI四象限：价格×OI联合判定趋势阶段
- 清算聚集区：磁吸效应预测价格运动方向
- 鲸鱼/新闻/恐贪：方向加权因子
"""

import logging
import time
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ═══════════════════ 数据结构 ═══════════════════


@dataclass
class FundingRegime:
    """资金费率状态"""
    rate: float = 0.0
    regime: str = "neutral"        # extreme_positive / positive / neutral / negative / extreme_negative
    signal: str = "neutral"        # bullish / bearish / neutral
    description: str = ""
    percentile: float = 50.0


@dataclass
class OIRegime:
    """OI四象限状态"""
    oi_change_pct: float = 0.0
    price_change_pct: float = 0.0
    quadrant: str = "unknown"      # long_buildup / short_buildup / short_covering / long_unwinding
    signal: str = "neutral"
    description: str = ""


@dataclass
class LiquidationAnalysis:
    """清算聚集区分析"""
    liq_long_1h: float = 0.0
    liq_short_1h: float = 0.0
    bias: str = "neutral"          # upward_magnet / downward_magnet / balanced
    signal: str = "neutral"
    cluster_above_pct: float = 0.0   # 上方清算占比
    cluster_below_pct: float = 0.0   # 下方清算占比
    description: str = ""


@dataclass
class TradingDirectionSignal:
    """最终交易方向信号"""
    symbol: str = "BTC"
    direction: str = "neutral"         # bullish / bearish / neutral
    confidence: int = 50               # 0-100
    funding: Optional[FundingRegime] = None
    oi: Optional[OIRegime] = None
    liquidation: Optional[LiquidationAnalysis] = None
    whale_direction: float = 0.0       # -1~+1
    whale_summary: str = ""
    news_sentiment: float = 0.0        # -1~+1
    news_top_event: str = ""
    fear_greed_index: float = 50.0
    long_short_ratio: float = 1.0
    top_trader_ls_ratio: float = 1.0
    predicted_funding_rate: float = 0.0
    sentiment_zone: str = "neutral"
    ai_reasoning: str = ""
    risk_level: str = "normal"         # safe / normal / warning / danger
    data_sources: str = ""
    timestamp: float = 0.0
    # [2026-08-15 消费端验收] 各来源可用性标记：字段仍为 sentinel 默认值
    #（funding=0/whale=0/news=0/fgi=50/ls=1.0）时标记 False，下游与 LLM
    # 提示词据此区分「真实中性」与「取数失败的中性」，不再冒充。
    sources_available: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["funding"] = asdict(self.funding) if self.funding else {}
        d["oi"] = asdict(self.oi) if self.oi else {}
        d["liquidation"] = asdict(self.liquidation) if self.liquidation else {}
        return d

    def to_prompt_text(self) -> str:
        """转换为可注入LLM Prompt的结构化文本"""
        lines = [
            "=== INTELLIGENCE TRADING SIGNAL ===",
            f"symbol: {self.symbol}",
            f"composite_direction: {self.direction} (confidence={self.confidence}%)",
            f"risk_level: {self.risk_level}",
            "",
        ]
        # [2026-08-15] 不可用来源显式列出（数据缺失 ≠ 真实中性）
        _unavailable = [k for k, v in (self.sources_available or {}).items() if not v]
        if _unavailable:
            lines.append("data_unavailable: " + ", ".join(_unavailable))
        if self.funding:
            lines.append(f"funding_rate: {self.funding.rate:.6f} ({self.funding.regime})")
            lines.append(f"funding_signal: {self.funding.signal} — {self.funding.description}")
        if self.oi:
            lines.append(f"oi_quadrant: {self.oi.quadrant}")
            lines.append(f"oi_signal: {self.oi.signal} — {self.oi.description}")
            lines.append(f"oi_change: {self.oi.oi_change_pct:+.2%}, price_change: {self.oi.price_change_pct:+.2%}")
        if self.liquidation:
            lines.append(f"liquidation_bias: {self.liquidation.bias}")
            lines.append(f"liquidation_long_1h: ${self.liquidation.liq_long_1h:,.0f}")
            lines.append(f"liquidation_short_1h: ${self.liquidation.liq_short_1h:,.0f}")
            if self.liquidation.description:
                lines.append(f"liquidation_note: {self.liquidation.description}")
        lines.append(f"whale_direction: {self.whale_direction:+.2f} — {self.whale_summary}")
        lines.append(f"news_sentiment: {self.news_sentiment:+.2f} — {self.news_top_event or 'N/A'}")
        lines.append(f"fear_greed_index: {self.fear_greed_index:.0f}/100 ({self.sentiment_zone})")
        lines.append(f"long_short_ratio: {self.long_short_ratio:.2f} (global)")
        lines.append(f"top_trader_ls_ratio: {self.top_trader_ls_ratio:.2f} (top traders)")
        if self.predicted_funding_rate != 0:
            lines.append(f"predicted_funding: {self.predicted_funding_rate:.6f}")
        if self.data_sources:
            lines.append(f"data_sources: {self.data_sources}")
        if self.ai_reasoning:
            lines.append(f"reasoning: {self.ai_reasoning}")
        return "\n".join(lines)


# ═══════════════════ 权重配置 ═══════════════════

COMPONENT_WEIGHTS = {
    "funding":     0.22,
    "oi":          0.22,
    "liquidation": 0.14,
    "whale":       0.10,
    "news":        0.08,
    "fear_greed":  0.06,
    "long_short":  0.10,
    "top_trader":  0.08,
}

# 资金费率阈值
FUNDING_EXTREME_POSITIVE = 0.0005
FUNDING_EXTREME_NEGATIVE = -0.0005
FUNDING_HYPER_POSITIVE = 0.001
FUNDING_HYPER_NEGATIVE = -0.001

# OI变化阈值
OI_SIGNIFICANT_CHANGE = 0.015   # 1.5% 即认为有意义
PRICE_SIGNIFICANT_CHANGE = 0.005  # 0.5%


# ═══════════════════ 引擎 ═══════════════════


class IntelligenceSignalEngine:
    """情报信号融合引擎（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._cache: Dict[str, TradingDirectionSignal] = {}
        self._cache_ts: Dict[str, float] = {}
        self._cache_ttl = 45
        self._weights: Dict[str, float] = dict(COMPONENT_WEIGHTS)
        self._direction_threshold = 0.15
        logger.info("[IntelSignal] 情报信号融合引擎初始化完成")

        # 尝试从信号反馈追踪器加载自适应权重
        try:
            from backend.database.connection import SessionLocal
            from backend.services.signal_feedback_tracker import signal_feedback_tracker
            _db = SessionLocal()
            try:
                signal_feedback_tracker.load_latest_weights(_db)
            finally:
                _db.close()
        except Exception as e:
            logger.debug(f"[IntelSignal] 自适应权重加载跳过: {e}")

    def load_weights(self, weights: Dict[str, float], direction_threshold: float = None):
        """加载外部权重配置（进化器优化后的权重）"""
        for k, v in weights.items():
            if k in self._weights:
                self._weights[k] = v
        if direction_threshold is not None:
            self._direction_threshold = direction_threshold
        self._cache.clear()
        logger.info(f"[IntelSignal] 已加载 {len(weights)} 个权重覆盖")

    def get_weights(self) -> Dict[str, float]:
        return dict(self._weights)

    def compute_trading_signal(self, symbol: str = "BTC") -> TradingDirectionSignal:
        """计算指定币种的综合交易方向信号"""
        now = time.time()
        key = symbol.upper()
        if key in self._cache and now - self._cache_ts.get(key, 0) < self._cache_ttl:
            return self._cache[key]

        # 防止缓存无限增长：超过 200 个 symbol 时清理最旧的条目
        if len(self._cache) > 200:
            oldest = sorted(self._cache_ts.items(), key=lambda x: x[1])[:100]
            for ok, _ in oldest:
                self._cache.pop(ok, None)
                self._cache_ts.pop(ok, None)

        signal = TradingDirectionSignal(symbol=key, timestamp=now)

        signal.funding = self._classify_funding_regime(key)
        signal.oi = self._classify_oi_regime(key)
        signal.liquidation = self._analyze_liquidation(key)
        self._fill_whale(signal)
        self._fill_news(signal)
        self._fill_sentiment(signal)
        self._fill_long_short(signal)

        # [2026-08-15] 来源可用性标记：值仍为 sentinel 默认 → unavailable
        signal.sources_available = {
            "funding": signal.funding is not None and signal.funding.rate != 0.0,
            "oi": signal.oi is not None and signal.oi.oi_change_pct != 0.0
                  and getattr(signal.oi, "quadrant", "") != "unavailable",
            "liquidation": signal.liquidation is not None and (
                signal.liquidation.liq_long_1h != 0.0 or signal.liquidation.liq_short_1h != 0.0
            ),
            "whale": signal.whale_summary not in ("", "暂无鲸鱼数据", "暂无该币种链上鲸鱼数据"),
            "news": signal.news_top_event != "" or signal.news_sentiment != 0.0,
            "sentiment": signal.fear_greed_index != 50.0,
            "ls_ratio": signal.long_short_ratio != 1.0,
        }

        self._compute_confluence(signal)

        self._cache[key] = signal
        self._cache_ts[key] = now
        return signal

    # ────────────────────── 资金费率状态 ──────────────────────

    def _classify_funding_regime(self, symbol: str) -> FundingRegime:
        regime = FundingRegime()
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(symbol)
            rate = snap.funding_rate
            regime.rate = rate

            if rate >= FUNDING_HYPER_POSITIVE:
                regime.regime = "extreme_positive"
                regime.signal = "bearish"
                regime.description = "多头极度拥挤，费率极高，注意回调风险"
            elif rate >= FUNDING_EXTREME_POSITIVE:
                regime.regime = "positive"
                regime.signal = "bearish"
                regime.description = "多头偏拥挤，费率偏高"
            elif rate <= FUNDING_HYPER_NEGATIVE:
                regime.regime = "extreme_negative"
                regime.signal = "bullish"
                regime.description = "空头极度拥挤，费率极低，可能反弹"
            elif rate <= FUNDING_EXTREME_NEGATIVE:
                regime.regime = "negative"
                regime.signal = "bullish"
                regime.description = "空头偏拥挤，费率偏低"
            else:
                regime.regime = "neutral"
                regime.signal = "neutral"
                regime.description = "费率正常范围，无极端偏向"

        except Exception as e:
            logger.debug(f"[IntelSignal] 资金费率获取失败: {e}")
        return regime

    # ────────────────────── OI四象限 ──────────────────────

    def _classify_oi_regime(self, symbol: str) -> OIRegime:
        regime = OIRegime()
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(symbol)
            oi_change = snap.oi_change_1h
            regime.oi_change_pct = oi_change

            price_change = self._get_price_change_1h(symbol)
            regime.price_change_pct = price_change
            # [2026-08-15] 价格变化取数失败（None）→ 象限无法判定，
            # 显式标 unavailable 返回（不再把缺失当 0 误判为 consolidation）。
            if price_change is None:
                regime.quadrant = "unavailable"
                regime.signal = "neutral"
                regime.description = "价格变化数据不可用，OI 象限无法判定"
                return regime

            oi_up = oi_change > OI_SIGNIFICANT_CHANGE
            oi_down = oi_change < -OI_SIGNIFICANT_CHANGE
            price_up = price_change > PRICE_SIGNIFICANT_CHANGE
            price_down = price_change < -PRICE_SIGNIFICANT_CHANGE

            if oi_up and price_up:
                regime.quadrant = "long_buildup"
                regime.signal = "bullish"
                regime.description = "多头建仓：价格和OI同步上升，趋势健康但注意过热"
            elif oi_up and price_down:
                regime.quadrant = "short_buildup"
                regime.signal = "bearish"
                regime.description = "空头建仓：OI增加但价格下跌，新空头入场看跌"
            elif oi_down and price_up:
                regime.quadrant = "short_covering"
                regime.signal = "neutral"
                regime.description = "空头平仓：价格上涨伴随OI下降，弱势反弹可能不持久"
            elif oi_down and price_down:
                regime.quadrant = "long_unwinding"
                regime.signal = "neutral"
                regime.description = "多头投降：价格和OI同步下降，可能接近阶段性底部"
            else:
                regime.quadrant = "consolidation"
                regime.signal = "neutral"
                regime.description = "震荡整理：OI和价格变化不显著，等待方向"

        except Exception as e:
            logger.debug(f"[IntelSignal] OI四象限计算失败: {e}")
        return regime

    def _get_price_change_1h(self, symbol: str):
        """获取1小时价格变化百分比；取数失败返回 None。

        [2026-08-15 消费端验收] 原失败返回 0.0，被 OI 四象限当「价格无变化」
        参与判定——把「取数失败」误判成「震荡整理」。改为 None，调用方
        显式处理缺失（quadrant=unavailable）。
        """
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import MarketAssetMetrics
            import time as _time

            db = MarketSessionLocal()
            try:
                now_ms = int(_time.time() * 1000)
                hour_ago_ms = now_ms - 3600 * 1000

                latest = db.query(MarketAssetMetrics).filter(
                    MarketAssetMetrics.symbol == symbol,
                    MarketAssetMetrics.mark_price.isnot(None),
                ).order_by(MarketAssetMetrics.timestamp.desc()).first()

                hour_ago = db.query(MarketAssetMetrics).filter(
                    MarketAssetMetrics.symbol == symbol,
                    MarketAssetMetrics.timestamp <= hour_ago_ms,
                    MarketAssetMetrics.mark_price.isnot(None),
                ).order_by(MarketAssetMetrics.timestamp.desc()).first()

                if latest and hour_ago and float(hour_ago.mark_price) > 0:
                    return (float(latest.mark_price) - float(hour_ago.mark_price)) / float(hour_ago.mark_price)
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[IntelSignal] 价格变化获取失败: {e}")
        return None

    # ────────────────────── 清算分析 ──────────────────────

    def _analyze_liquidation(self, symbol: str) -> LiquidationAnalysis:
        analysis = LiquidationAnalysis()
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(symbol)

            analysis.liq_long_1h = snap.liquidation_1h_long
            analysis.liq_short_1h = snap.liquidation_1h_short

            total = analysis.liq_long_1h + analysis.liq_short_1h
            if total > 0:
                analysis.cluster_above_pct = analysis.liq_short_1h / total
                analysis.cluster_below_pct = analysis.liq_long_1h / total

            # 多头清算多 = 下方密集 = 价格可能继续向下磁吸
            # 空头清算多 = 上方密集 = 价格可能继续向上磁吸
            if total < 100_000:
                analysis.bias = "balanced"
                analysis.signal = "neutral"
                analysis.description = "清算量级小，无显著磁吸效应"
            elif analysis.liq_short_1h > analysis.liq_long_1h * 2:
                analysis.bias = "upward_magnet"
                analysis.signal = "bullish"
                analysis.description = f"空头清算是多头的{analysis.liq_short_1h/max(analysis.liq_long_1h,1):.1f}倍，上方有清算磁吸"
            elif analysis.liq_long_1h > analysis.liq_short_1h * 2:
                analysis.bias = "downward_magnet"
                analysis.signal = "bearish"
                analysis.description = f"多头清算是空头的{analysis.liq_long_1h/max(analysis.liq_short_1h,1):.1f}倍，下方有清算磁吸"
            else:
                analysis.bias = "balanced"
                analysis.signal = "neutral"
                analysis.description = "多空清算相对均衡"

        except Exception as e:
            logger.debug(f"[IntelSignal] 清算分析失败: {e}")
        return analysis

    # ────────────────────── 鲸鱼 ──────────────────────

    def _fill_whale(self, signal: TradingDirectionSignal):
        try:
            from backend.services.whale_tracker_service import whale_tracker
            ws = whale_tracker.get_whale_signal(signal.symbol)
            signal.whale_direction = ws.direction
            signal.whale_summary = ws.summary or "无重大异动"
        except Exception as e:
            logger.debug(f"[IntelSignal] 鲸鱼数据获取失败: {e}")

    # ────────────────────── 新闻 ──────────────────────

    def _fill_news(self, signal: TradingDirectionSignal):
        try:
            from backend.services.news_intelligence_service import news_intelligence
            agg = news_intelligence.get_aggregate_sentiment(signal.symbol, hours=24)
            signal.news_sentiment = agg

            recent = news_intelligence.get_recent_signals(signal.symbol, hours=24, limit=1)
            if recent:
                signal.news_top_event = recent[0].get("title", "")
        except Exception as e:
            logger.debug(f"[IntelSignal] 新闻数据获取失败: {e}")

    # ────────────────────── 情绪/恐贪 ──────────────────────

    def _fill_sentiment(self, signal: TradingDirectionSignal):
        try:
            from backend.services.sentiment_composite_service import sentiment_composite
            result = sentiment_composite.calculate(signal.symbol)
            signal.fear_greed_index = result.index
            signal.sentiment_zone = result.zone
        except Exception as e:
            logger.debug(f"[IntelSignal] 情绪数据获取失败: {e}")

    # ────────────────────── 多空比 ──────────────────────

    def _fill_long_short(self, signal: TradingDirectionSignal):
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(signal.symbol)
            signal.long_short_ratio = snap.long_short_ratio
            signal.top_trader_ls_ratio = snap.top_trader_ls_ratio
            signal.predicted_funding_rate = snap.predicted_funding_rate
            signal.data_sources = snap.data_sources
        except Exception as e:
            logger.debug(f"[IntelSignal] 多空比获取失败: {e}")

    # ────────────────────── 汇流评分 ──────────────────────

    def _compute_confluence(self, signal: TradingDirectionSignal):
        """按权重汇流所有子信号，输出最终方向和置信度"""

        scores: Dict[str, float] = {}

        # 费率：看跌信号时给负分
        if signal.funding:
            scores["funding"] = self._signal_to_score(signal.funding.signal)

        # OI
        if signal.oi:
            scores["oi"] = self._signal_to_score(signal.oi.signal)

        # 清算
        if signal.liquidation:
            scores["liquidation"] = self._signal_to_score(signal.liquidation.signal)

        # 鲸鱼 (-1~+1 直接用)
        scores["whale"] = max(-1, min(1, signal.whale_direction))

        # 新闻 (-1~+1 直接用)
        scores["news"] = max(-1, min(1, signal.news_sentiment))

        # 恐贪指数 (0~100 → -1~+1)
        scores["fear_greed"] = (signal.fear_greed_index - 50) / 50

        # 多空比 (反向指标：ratio>1 表示多头多→反向看空)
        ratio = signal.long_short_ratio
        if ratio > 0:
            scores["long_short"] = max(-1, min(1, (1 - ratio) * 2))
        else:
            scores["long_short"] = 0

        # 大户多空比（大户行为更可信，同样反向）
        top_ratio = signal.top_trader_ls_ratio
        if top_ratio > 0:
            scores["top_trader"] = max(-1, min(1, (1 - top_ratio) * 2))
        else:
            scores["top_trader"] = 0

        weighted_sum = 0.0
        total_weight = 0.0
        for component, weight in self._weights.items():
            if component in scores:
                weighted_sum += scores[component] * weight
                total_weight += weight

        if total_weight > 0:
            composite = weighted_sum / total_weight
        else:
            composite = 0.0

        thresh = self._direction_threshold
        if composite > thresh:
            signal.direction = "bullish"
        elif composite < -thresh:
            signal.direction = "bearish"
        else:
            signal.direction = "neutral"

        # 置信度 (composite 的绝对值映射到 0~100)
        signal.confidence = min(100, max(0, int(abs(composite) * 100)))

        # 风险等级
        risk_factors = 0
        if signal.funding and signal.funding.regime.startswith("extreme"):
            risk_factors += 1
        if signal.liquidation and (signal.liquidation.liq_long_1h + signal.liquidation.liq_short_1h) > 10_000_000:
            risk_factors += 1
        if signal.fear_greed_index < 20 or signal.fear_greed_index > 80:
            risk_factors += 1

        if risk_factors >= 3:
            signal.risk_level = "danger"
        elif risk_factors >= 2:
            signal.risk_level = "warning"
        elif risk_factors >= 1:
            signal.risk_level = "caution"
        else:
            signal.risk_level = "normal"

        # AI reasoning (给前端和 Prompt 用的汇总)
        dir_cn = {"bullish": "看多", "bearish": "看空", "neutral": "观望"}.get(signal.direction, "观望")
        parts = []
        if signal.funding and signal.funding.signal != "neutral":
            parts.append(f"费率{signal.funding.description}")
        if signal.oi and signal.oi.signal != "neutral":
            parts.append(f"OI{signal.oi.description}")
        if signal.liquidation and signal.liquidation.signal != "neutral":
            parts.append(f"清算{signal.liquidation.description}")
        if abs(signal.whale_direction) > 0.3:
            parts.append(f"鲸鱼{'偏多' if signal.whale_direction > 0 else '偏空'}")
        if abs(signal.news_sentiment) > 0.2:
            parts.append(f"新闻{'利多' if signal.news_sentiment > 0 else '利空'}")

        signal.ai_reasoning = f"综合信号{dir_cn}({signal.confidence}%)。" + "；".join(parts[:3]) if parts else f"综合信号{dir_cn}({signal.confidence}%)，各维度分歧不大"

    @staticmethod
    def _signal_to_score(sig: str) -> float:
        return {"bullish": 1.0, "bearish": -1.0}.get(sig, 0.0)


# 全局单例
intelligence_signal_engine = IntelligenceSignalEngine()
