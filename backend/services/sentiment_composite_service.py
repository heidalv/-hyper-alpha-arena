"""
市场情绪综合指数 — 融合7个因子输出0~100单一指数

因子:
1. news_sentiment     (20%) — 新闻情报AI评分
2. funding_rate       (15%) — 资金费率极端度
3. oi_change          (15%) — OI变化方向
4. whale_activity     (15%) — 鲸鱼异动方向
5. long_short_ratio   (10%) — 多空比（反向指标）
6. technical_composite(15%) — RSI+MACD+BB综合
7. fear_greed_index   (10%) — Alternative.me指数
"""
import logging
import time
import threading
from dataclasses import dataclass
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    index: float = 50.0         # 0~100
    zone: str = "neutral"       # extreme_fear / fear / neutral / greed / extreme_greed
    factors: Dict[str, float] = None
    trading_guidance: str = ""
    timestamp: float = 0.0
    # [2026-07-10] available=False 表示 index 是因子取数失败后坍缩到的中性占位，
    # 而非真实计算的中性。调用方应据此决定是否把情绪数据注入 AI prompt。
    available: bool = True

    def __post_init__(self):
        if self.factors is None:
            self.factors = {}


ZONE_THRESHOLDS = [
    (15, "extreme_fear", "市场可能超卖，观察抄底机会，但不追空"),
    (35, "fear", "谨慎做多，小仓位试探"),
    (65, "neutral", "跟随技术面趋势"),
    (85, "greed", "注意止盈，不追高，准备减仓"),
    (100, "extreme_greed", "减仓/对冲，严格止盈"),
]


class SentimentCompositeService:
    """市场情绪综合指数（单例）"""

    _instance = None
    _lock = threading.Lock()

    FACTOR_WEIGHTS = {
        "news_sentiment":      0.20,
        "funding_rate":        0.15,
        "oi_change":           0.15,
        "whale_activity":      0.15,
        "long_short_ratio":    0.10,
        "technical_composite": 0.15,
        "fear_greed_index":    0.10,
    }

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
        self._cache: Dict[str, SentimentResult] = {}
        self._cache_ts: float = 0
        self._fear_greed_cache: Optional[float] = None
        self._fear_greed_ts: float = 0
        logger.info("[SentimentIndex] 综合情绪指数服务初始化完成")

    def calculate(self, symbol: str = "BTC") -> SentimentResult:
        """计算综合情绪指数（带120秒缓存）"""
        now = time.time()
        key = symbol.upper()
        if key in self._cache and now - self._cache_ts < 120:
            return self._cache[key]

        factors = {}

        # 因子1: 新闻情绪
        factors["news_sentiment"] = self._get_news_factor(symbol)

        # 因子2: 资金费率
        factors["funding_rate"] = self._get_funding_factor(symbol)

        # 因子3: OI变化
        factors["oi_change"] = self._get_oi_factor(symbol)

        # 因子4: 鲸鱼异动
        factors["whale_activity"] = self._get_whale_factor(symbol)

        # 因子5: 多空比（反向指标）
        factors["long_short_ratio"] = self._get_ls_ratio_factor(symbol)

        # 因子6: 技术面综合
        factors["technical_composite"] = self._get_technical_factor(symbol)

        # 因子7: 恐惧贪婪指数
        factors["fear_greed_index"] = self._get_fear_greed_factor()

        # 加权计算
        weighted_sum = sum(
            factors.get(k, 50) * w for k, w in self.FACTOR_WEIGHTS.items()
        )
        index = max(0, min(100, weighted_sum))

        # [2026-07-10] 数据可用性判定：各子因子取数失败时默认返回 50.0，
        # 若超过半数因子 == 50.0（精确等于，真实计算极少恰好50），说明多数数据源失败，
        # 此时的 index 是坍缩的中性占位而非真实计算结果 → 标记 available=False。
        factors_at_default = sum(1 for v in factors.values() if abs(v - 50.0) < 0.01)
        is_available = factors_at_default < len(self.FACTOR_WEIGHTS) / 2

        # 判定区间
        zone = "neutral"
        guidance = ""
        for threshold, z, g in ZONE_THRESHOLDS:
            if index <= threshold:
                zone = z
                guidance = g
                break

        result = SentimentResult(
            index=round(index, 1),
            zone=zone,
            factors=factors,
            trading_guidance=guidance,
            timestamp=now,
            available=is_available,
        )
        self._cache[key] = result
        self._cache_ts = now
        return result

    # ────────────────────── 各因子获取 ──────────────────────

    def _get_news_factor(self, symbol: str) -> float:
        """新闻情绪 → 0~100"""
        try:
            from backend.services.news_intelligence_service import news_intelligence
            sentiment = news_intelligence.get_aggregate_sentiment(symbol, hours=4)
            return 50 + sentiment * 50  # -1~+1 → 0~100
        except Exception:
            return 50.0

    def _get_funding_factor(self, symbol: str) -> float:
        """资金费率 → 0~100（正=贪婪，极端负=恐惧）"""
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(symbol)
            rate = snap.funding_rate
            # 正常范围 -0.001~0.001 → 映射到 0~100
            normalized = (rate + 0.001) / 0.002 * 100
            return max(0, min(100, normalized))
        except Exception:
            return 50.0

    def _get_oi_factor(self, symbol: str) -> float:
        """OI变化 → 0~100"""
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(symbol)
            change = snap.oi_change_1h
            # -5%~+5% → 0~100
            return max(0, min(100, 50 + change * 1000))
        except Exception:
            return 50.0

    def _get_whale_factor(self, symbol: str) -> float:
        """鲸鱼异动 → 0~100"""
        try:
            from backend.services.whale_tracker_service import whale_tracker
            sig = whale_tracker.get_whale_signal(symbol)
            return 50 + sig.direction * 50
        except Exception:
            return 50.0

    def _get_ls_ratio_factor(self, symbol: str) -> float:
        """多空比（反向）→ 0~100。多头过多=恐惧（反向看空）"""
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            snap = derivatives_analytics.get_snapshot(symbol)
            ratio = snap.long_short_ratio
            # ratio>1 多头多 → 反向=偏空; <1 空头多 → 反向=偏多
            inv = 1.0 / max(0.1, ratio)
            return max(0, min(100, inv * 50))
        except Exception:
            return 50.0

    def _get_technical_factor(self, symbol: str) -> float:
        """RSI+趋势方向 → 0~100 (从 UnifiedDataPool 快照获取)"""
        try:
            from backend.services.unified_data_pool import unified_data_pool
            snap = unified_data_pool.get_snapshot(max_age=60)
            if snap and symbol in snap.indicators:
                rsi = snap.indicators[symbol].get("rsi")
                if rsi is not None:
                    return max(0, min(100, rsi))
        except Exception:
            pass
        return 50.0

    def _get_fear_greed_factor(self) -> float:
        """Alternative.me 恐惧贪婪指数"""
        now = time.time()
        if self._fear_greed_cache is not None and now - self._fear_greed_ts < 3600:
            return self._fear_greed_cache

        try:
            import os as _os
            proxy = _os.environ.get("BINANCE_HTTPS_PROXY") or None
            with httpx.Client(timeout=10, proxy=proxy) as client:
                r = client.get("https://api.alternative.me/fng/?limit=1")
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    if data:
                        val = float(data[0].get("value", 50))
                        self._fear_greed_cache = val
                        self._fear_greed_ts = now
                        return val
        except Exception as e:
            logger.debug(f"[SentimentIndex] Fear&Greed API 异常: {e}")

        return self._fear_greed_cache if self._fear_greed_cache is not None else 50.0


sentiment_composite = SentimentCompositeService()
