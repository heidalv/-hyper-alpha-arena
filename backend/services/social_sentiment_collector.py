"""
社交情绪数据采集器 — 聚合多源社交/新闻情绪信号

数据源:
- CryptoPanic API (免费tier: 新闻聚合+社区投票情绪)
- LunarCrush API  (免费tier: 社交活跃度指标)
- Reddit /r/cryptocurrency (公开 JSON API, 无需 key)

缓存策略:
- CryptoPanic: 30 min TTL
- LunarCrush:  60 min TTL
- Reddit:     120 min TTL
- 异常时返回中性值, 不阻塞主流程

输出字段注入到 unified_data_pool DataFrame:
  social_score, news_sentiment, discussion_volume, sentiment_change_24h
"""

import logging
import time
import os
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Symbol → CryptoPanic currency code mapping
_SYMBOL_TO_CURRENCY = {
    "BTC": "BTC",
    "ETH": "ETH",
    "SOL": "SOL",
    "DOGE": "DOGE",
    "XRP": "XRP",
    "ADA": "ADA",
    "AVAX": "AVAX",
    "LINK": "LINK",
    "DOT": "DOT",
    "MATIC": "MATIC",
    "ARB": "ARB",
    "OP": "OP",
    "SUI": "SUI",
    "APT": "APT",
}


@dataclass
class SentimentData:
    """Per-symbol aggregated sentiment snapshot."""
    social_score: float = 50.0       # 0-100 composite
    news_sentiment: float = 0.0      # -1 (bearish) to +1 (bullish)
    discussion_volume: float = 0.0   # relative volume vs 7d avg
    sentiment_change_24h: float = 0.0  # score delta last 24h
    sources_available: int = 0


class SocialSentimentCollector:
    """Multi-source social sentiment aggregator."""

    CRYPTOPANIC_TTL = 1800   # 30 min
    LUNARCRUSH_TTL = 3600    # 60 min
    REDDIT_TTL = 7200        # 120 min
    REQUEST_TIMEOUT = 10

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cryptopanic_key: Optional[str] = os.getenv("CRYPTOPANIC_API_KEY")
        self._lunarcrush_key: Optional[str] = os.getenv("LUNARCRUSH_API_KEY")

    # ── public API ───────────────────────────────

    def collect_all(self, symbols: List[str]) -> Dict[str, SentimentData]:
        """Collect sentiment for all symbols, return {symbol: SentimentData}."""
        result: Dict[str, SentimentData] = {}
        for symbol in symbols:
            result[symbol] = self._collect_symbol(symbol)
        return result

    def collect_flat(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Return flat dict suitable for DataFrame injection."""
        raw = self.collect_all(symbols)
        return {
            sym: {
                "social_score": d.social_score,
                "news_sentiment": d.news_sentiment,
                "discussion_volume": d.discussion_volume,
                "sentiment_change_24h": d.sentiment_change_24h,
            }
            for sym, d in raw.items()
        }

    # ── per-symbol aggregation ───────────────────

    def _collect_symbol(self, symbol: str) -> SentimentData:
        scores: List[float] = []
        sentiments: List[float] = []
        volumes: List[float] = []
        sources = 0

        cp = self._fetch_cryptopanic(symbol)
        if cp:
            sources += 1
            scores.append(cp.get("score", 50.0))
            sentiments.append(cp.get("sentiment", 0.0))
            volumes.append(cp.get("volume", 0.0))

        lc = self._fetch_lunarcrush(symbol)
        if lc:
            sources += 1
            scores.append(lc.get("score", 50.0))
            sentiments.append(lc.get("sentiment", 0.0))
            volumes.append(lc.get("volume", 0.0))

        rd = self._fetch_reddit_mentions(symbol)
        if rd:
            sources += 1
            scores.append(rd.get("score", 50.0))
            sentiments.append(rd.get("sentiment", 0.0))
            volumes.append(rd.get("volume", 0.0))

        if not scores:
            return SentimentData()

        return SentimentData(
            social_score=sum(scores) / len(scores),
            news_sentiment=sum(sentiments) / len(sentiments),
            discussion_volume=sum(volumes) / len(volumes),
            sentiment_change_24h=0.0,
            sources_available=sources,
        )

    # ── CryptoPanic ──────────────────────────────

    def _fetch_cryptopanic(self, symbol: str) -> Optional[Dict[str, float]]:
        cache_key = f"cryptopanic:{symbol}"
        cached = self._get_cache(cache_key, self.CRYPTOPANIC_TTL)
        if cached is not None:
            return cached

        currency = _SYMBOL_TO_CURRENCY.get(symbol, symbol)
        try:
            import urllib.request
            import json

            url = f"https://cryptopanic.com/api/free/v1/posts/?auth_token={self._cryptopanic_key}&currencies={currency}&kind=news&public=true"
            if not self._cryptopanic_key:
                url = f"https://cryptopanic.com/api/free/v1/posts/?currencies={currency}&kind=news&public=true"

            req = urllib.request.Request(url, headers={"User-Agent": "HyperAlphaArena/1.0"})
            with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())

            posts = data.get("results", [])
            if not posts:
                result = {"score": 50.0, "sentiment": 0.0, "volume": 0.0}
                self._set_cache(cache_key, result)
                return result

            bullish = sum(1 for p in posts if p.get("votes", {}).get("positive", 0) > p.get("votes", {}).get("negative", 0))
            bearish = sum(1 for p in posts if p.get("votes", {}).get("negative", 0) > p.get("votes", {}).get("positive", 0))
            total = len(posts)

            sentiment = (bullish - bearish) / max(total, 1)
            score = 50.0 + sentiment * 50.0

            result = {
                "score": max(0.0, min(100.0, score)),
                "sentiment": max(-1.0, min(1.0, sentiment)),
                "volume": float(total),
            }
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[SocialSentiment] CryptoPanic {symbol} failed: {e}")
            return None

    # ── LunarCrush ───────────────────────────────

    def _fetch_lunarcrush(self, symbol: str) -> Optional[Dict[str, float]]:
        if not self._lunarcrush_key:
            return None

        cache_key = f"lunarcrush:{symbol}"
        cached = self._get_cache(cache_key, self.LUNARCRUSH_TTL)
        if cached is not None:
            return cached

        try:
            import urllib.request
            import json

            url = f"https://lunarcrush.com/api4/public/coins/{symbol}/v1"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self._lunarcrush_key}",
                "User-Agent": "HyperAlphaArena/1.0",
            })
            with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())

            coin_data = data.get("data", {})
            galaxy_score = coin_data.get("galaxy_score", 50.0)
            alt_rank = coin_data.get("alt_rank", 500)
            social_volume = coin_data.get("social_volume", 0)
            sentiment_score = coin_data.get("sentiment", 50)

            # Normalize to 0-100
            norm_score = float(galaxy_score) if galaxy_score else 50.0
            norm_sentiment = (float(sentiment_score) - 50.0) / 50.0 if sentiment_score else 0.0

            result = {
                "score": max(0.0, min(100.0, norm_score)),
                "sentiment": max(-1.0, min(1.0, norm_sentiment)),
                "volume": float(social_volume),
            }
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[SocialSentiment] LunarCrush {symbol} failed: {e}")
            return None

    # ── Reddit ───────────────────────────────────

    def _fetch_reddit_mentions(self, symbol: str) -> Optional[Dict[str, float]]:
        cache_key = f"reddit:{symbol}"
        cached = self._get_cache(cache_key, self.REDDIT_TTL)
        if cached is not None:
            return cached

        try:
            import urllib.request
            import json

            url = f"https://www.reddit.com/r/cryptocurrency/search.json?q={symbol}&sort=new&t=day&limit=25"
            req = urllib.request.Request(url, headers={"User-Agent": "HyperAlphaArena/1.0 (research)"})
            with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())

            posts = data.get("data", {}).get("children", [])
            if not posts:
                result = {"score": 50.0, "sentiment": 0.0, "volume": 0.0}
                self._set_cache(cache_key, result)
                return result

            total_score = sum(p["data"].get("score", 0) for p in posts)
            upvote_ratio_avg = sum(p["data"].get("upvote_ratio", 0.5) for p in posts) / len(posts)
            num_comments = sum(p["data"].get("num_comments", 0) for p in posts)

            # upvote_ratio > 0.6 → bullish lean; < 0.4 → bearish
            sentiment = (upvote_ratio_avg - 0.5) * 2.0
            # Volume: number of posts * avg engagement
            volume = float(len(posts)) * (1.0 + num_comments / max(len(posts), 1) * 0.1)
            score = 50.0 + sentiment * 30.0 + min(len(posts), 25) * 0.4

            result = {
                "score": max(0.0, min(100.0, score)),
                "sentiment": max(-1.0, min(1.0, sentiment)),
                "volume": volume,
            }
            self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.debug(f"[SocialSentiment] Reddit {symbol} failed: {e}")
            return None

    # ── cache helpers ────────────────────────────

    def _get_cache(self, key: str, ttl: float) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry and (time.time() - entry[0]) < ttl:
            return entry[1]
        return None

    def _set_cache(self, key: str, value: Any):
        self._cache[key] = (time.time(), value)


# Global singleton
_collector: Optional[SocialSentimentCollector] = None


def get_social_sentiment_collector() -> SocialSentimentCollector:
    global _collector
    if _collector is None:
        _collector = SocialSentimentCollector()
    return _collector
