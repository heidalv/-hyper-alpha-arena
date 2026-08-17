"""
新闻情报服务 — CryptoPanic + RSS + LLM影响分析

流程: 定时拉取 → 去重 → LLM分析影响 → 写入DB → 推送快照
"""
import asyncio
import hashlib
import logging
import os
import re
import time
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _try_parse_pub(raw: str) -> bool:
    """判断 RSS/CryptoPanic 发布时间字符串是否可解析为 datetime。"""
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


@dataclass
class NewsImpact:
    direction: float = 0.0      # -1 ~ +1
    strength: int = 1           # 1~5
    duration: str = "short"     # short / medium / long
    symbols: List[str] = field(default_factory=lambda: ["BTC"])
    category: str = "general"
    confidence: float = 0.5
    summary: str = ""


RSS_FEEDS = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("decrypt", "https://decrypt.co/feed"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
]


class NewsIntelligenceService:
    """新闻情报服务（单例）"""

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
        self._cryptopanic_key = self._load_cryptopanic_key()
        self._seen_hashes: set = set()
        self._latest_events: List[Dict] = []
        self._latest_ts: float = 0
        logger.info(f"[NewsIntel] 新闻情报服务初始化完成 (CryptoPanic={'有Key' if self._cryptopanic_key else '无Key'})")

    @staticmethod
    def _load_cryptopanic_key() -> str:
        key = os.environ.get("CRYPTOPANIC_API_KEY", "")
        if key:
            return key
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemConfig
            db = SessionLocal()
            try:
                cfg = db.query(SystemConfig).filter(SystemConfig.key == "CRYPTOPANIC_API_KEY").first()
                if cfg and cfg.value:
                    os.environ["CRYPTOPANIC_API_KEY"] = cfg.value
                    return cfg.value
            finally:
                db.close()
        except Exception:
            pass
        return ""

    # ────────────────────────── public ──────────────────────────

    def start_fast_loop(self, interval_sec: float = 90.0) -> None:
        """突发新闻快通道：仅 CryptoPanic `filter=important` 高频轮询（默认 90s），
        每轮最多分析 3 条，LLM 标注后落库。与 5 分钟全量通道共享去重集合。

        [2026-08-15 D7] 原只有 5 分钟全量轮询，突发新闻（黑客/ETF/监管）
        最坏延迟 ~5 分钟+；快通道把突发新闻入库延迟压到 ~90s。
        """
        import threading as _threading

        if not self._cryptopanic_key:
            logger.info("[NewsIntel] 无 CryptoPanic key，突发快通道跳过")
            return
        if getattr(self, "_fast_loop_started", False):
            return
        self._fast_loop_started = True

        def _run() -> None:
            import asyncio as _asyncio
            while True:
                try:
                    _asyncio.run(self._fast_cycle())
                except Exception as exc:
                    logger.debug("[NewsIntel] 快通道异常: %s", exc)
                time.sleep(max(30.0, float(interval_sec)))

        t = _threading.Thread(target=_run, name="news-fast-channel", daemon=True)
        t.start()
        logger.info(
            "[NewsIntel] 突发快通道已启动（%.0fs，CryptoPanic important，每轮≤3条）",
            interval_sec,
        )

    async def _fast_cycle(self) -> None:
        import httpx

        if not self._cryptopanic_key:
            return
        proxy = os.environ.get("BINANCE_HTTPS_PROXY") or None
        try:
            async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
                items = await self._fetch_cryptopanic(client)
            new_items = self._deduplicate(items)
            if not new_items:
                return
            from backend.database.connection import MarketSessionLocal
            db = MarketSessionLocal()
            try:
                for item in new_items[:3]:
                    impact = await self._analyze_with_llm(item)
                    self._save_to_db(db, item, impact)
            finally:
                db.close()
        except Exception as exc:
            logger.debug("[NewsIntel] 快通道抓取失败: %s", exc)

    async def fetch_and_analyze(self, db: Session) -> List[Dict]:
        """主流程: 拉取 → 去重 → LLM分析 → 存DB"""
        raw_items = await self._fetch_all_sources()
        new_items = self._deduplicate(raw_items)
        if not new_items:
            logger.debug("[NewsIntel] 无新增新闻")
            return []

        results = []
        for item in new_items[:10]:  # 每轮最多分析10条
            impact = await self._analyze_with_llm(item)
            record = self._save_to_db(db, item, impact)
            results.append(record)

        self._latest_events = results
        self._latest_ts = time.time()
        logger.info(f"[NewsIntel] 分析了 {len(results)} 条新闻")
        return results

    def get_recent_signals(self, symbol: str = "BTC", hours: int = 24, limit: int = 20) -> List[Dict]:
        """获取最近N小时的新闻信号"""
        try:
            import json as _json
            from sqlalchemy import text
            from backend.database.connection import MarketSessionLocal
            db = MarketSessionLocal()
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
                rows = db.execute(text(
                    "SELECT id, source, title, url, impact_direction, impact_strength, "
                    "impact_duration, affected_symbols, event_category, confidence, "
                    "ai_summary, created_at "
                    "FROM news_events WHERE created_at >= :cutoff "
                    "ORDER BY created_at DESC LIMIT :lim"
                ), {"cutoff": cutoff, "lim": limit}).fetchall()
                results = []
                sym_upper = symbol.upper()
                for r in rows:
                    raw_syms = r[7]
                    if isinstance(raw_syms, str):
                        try:
                            syms = _json.loads(raw_syms)
                        except Exception:
                            syms = []
                    elif isinstance(raw_syms, list):
                        syms = raw_syms
                    else:
                        syms = []
                    if sym_upper in [s.upper() for s in syms] or not syms:
                        results.append({
                            "id": r[0],
                            "title": r[2],
                            "source": r[1],
                            "url": r[3] or "",
                            "direction": r[4],
                            "strength": r[5],
                            "duration": r[6],
                            "category": r[8],
                            "confidence": r[9],
                            "summary": r[10],
                            "created_at": str(r[11]),
                        })
                return results
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[NewsIntel] get_recent_signals 异常: {e}")
            return []

    def get_aggregate_sentiment(self, symbol: str = "BTC", hours: int = 24) -> float:
        """汇总最近新闻的情绪方向，-1~+1"""
        signals = self.get_recent_signals(symbol, hours)
        if not signals:
            return 0.0
        weighted_sum = sum(
            (s.get("direction", 0) or 0) * (s.get("confidence", 0.5) or 0.5)
            for s in signals
        )
        total_weight = sum(s.get("confidence", 0.5) or 0.5 for s in signals)
        return weighted_sum / total_weight if total_weight else 0.0

    def get_symbol_sentiment(self, symbol: str, hours: int = 24) -> Dict[str, Any]:
        """AutoCoin Phase2 专用：单币新闻情绪快照（无信号时 available=False，不伪造涨跌情绪）。

        返回:
          sentiment(-1~+1) / sentiment_label / social_volume / top_events /
          freshness_min / available
        """
        min_conf = float(os.getenv("AUTO_COIN_NEWS_MIN_CONF", "0.3"))
        signals = self.get_recent_signals(symbol, hours=hours, limit=30)
        # 只保留真正命中该币、且置信度够的事件（排除「syms 为空被放行」的噪声）
        sym_upper = (symbol or "").upper()
        filtered: List[Dict] = []
        for s in signals:
            conf = float(s.get("confidence", 0.5) or 0.5)
            if conf < min_conf:
                continue
            # get_recent_signals 在 syms 为空时也会返回；这里再收紧一次
            title = (s.get("title") or "").upper()
            summary = (s.get("summary") or "").upper()
            if sym_upper and (
                sym_upper in title
                or sym_upper in summary
                or sym_upper == "BTC"  # BTC 允许宏观新闻
            ):
                filtered.append(s)
            elif not sym_upper:
                filtered.append(s)

        if not filtered and signals:
            # 回退：接受 get_recent_signals 的 affected_symbols 命中结果
            filtered = [s for s in signals if float(s.get("confidence", 0.5) or 0.5) >= min_conf]

        if not filtered:
            return {
                "sentiment": 0.0,
                "sentiment_label": "neutral",
                "social_volume": 0,
                "top_events": [],
                "freshness_min": None,
                "available": False,
            }

        weighted_sum = sum(
            (s.get("direction", 0) or 0) * (s.get("confidence", 0.5) or 0.5)
            for s in filtered
        )
        total_weight = sum(s.get("confidence", 0.5) or 0.5 for s in filtered)
        sentiment = weighted_sum / total_weight if total_weight else 0.0
        sentiment = max(-1.0, min(1.0, float(sentiment)))

        if sentiment > 0.25:
            label = "bullish"
        elif sentiment < -0.25:
            label = "bearish"
        else:
            label = "neutral"

        freshness_min = None
        try:
            created = filtered[0].get("created_at")
            if created:
                # 支持 "YYYY-MM-DD HH:MM:SS" / ISO
                ts = str(created).replace("T", " ").split(".")[0]
                dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                freshness_min = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 60.0)
        except Exception:
            freshness_min = None

        top_events = [
            {
                "title": (s.get("title") or "")[:120],
                "direction": s.get("direction"),
                "strength": s.get("strength"),
                "confidence": s.get("confidence"),
            }
            for s in filtered[:3]
        ]

        return {
            "sentiment": round(sentiment, 4),
            "sentiment_label": label,
            "social_volume": len(filtered),
            "top_events": top_events,
            "freshness_min": round(freshness_min, 1) if freshness_min is not None else None,
            "available": True,
        }

    # ────────────────────────── fetch ──────────────────────────

    async def _fetch_all_sources(self) -> List[Dict]:
        items = []
        proxy = os.environ.get("BINANCE_HTTPS_PROXY") or None
        async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
            tasks = []
            if self._cryptopanic_key:
                tasks.append(self._fetch_cryptopanic(client))
            tasks.append(self._fetch_rss(client))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    items.extend(r)
        return items

    async def _fetch_cryptopanic(self, client: httpx.AsyncClient) -> List[Dict]:
        try:
            url = (
                f"https://cryptopanic.com/api/v1/posts/"
                f"?auth_token={self._cryptopanic_key}"
                f"&filter=important&currencies=BTC,ETH,SOL"
            )
            r = await client.get(url)
            if r.status_code != 200:
                logger.debug(f"[NewsIntel] CryptoPanic 响应 {r.status_code}")
                return []
            data = r.json()
            items = []
            for p in data.get("results", [])[:15]:
                items.append({
                    "source": "cryptopanic",
                    "title": p.get("title", ""),
                    "url": p.get("url", ""),
                    "published_at": p.get("published_at", ""),
                    "currencies": [c.get("code", "") for c in p.get("currencies", [])],
                    "votes": p.get("votes", {}),
                })
            return items
        except Exception as e:
            logger.debug(f"[NewsIntel] CryptoPanic 获取失败: {e}")
            return []

    async def _fetch_rss(self, client: httpx.AsyncClient) -> List[Dict]:
        items = []
        for name, url in RSS_FEEDS:
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.text[:50000])
                for item_el in root.iter("item"):
                    title_el = item_el.find("title")
                    link_el = item_el.find("link")
                    pub_el = item_el.find("pubDate")
                    if title_el is not None and title_el.text:
                        items.append({
                            "source": name,
                            "title": title_el.text.strip(),
                            "url": link_el.text.strip() if link_el is not None and link_el.text else "",
                            "published_at": pub_el.text.strip() if pub_el is not None and pub_el.text else "",
                            "currencies": [],
                            "votes": {},
                        })
            except Exception as e:
                logger.debug(f"[NewsIntel] RSS {name} 获取失败: {e}")
        return items

    # ────────────────────────── dedup ──────────────────────────

    def _deduplicate(self, items: List[Dict]) -> List[Dict]:
        unique = []
        for item in items:
            h = hashlib.md5(item["title"].encode()).hexdigest()
            if h not in self._seen_hashes:
                self._seen_hashes.add(h)
                unique.append(item)
        # 限制 seen_hashes 大小
        if len(self._seen_hashes) > 5000:
            self._seen_hashes = set(list(self._seen_hashes)[-2500:])
        return unique

    # ────────────────────────── LLM分析 ──────────────────────────

    async def _analyze_with_llm(self, item: Dict) -> NewsImpact:
        try:
            from backend.services.llm_config_service import call_llm_api_sync, get_llm_config
            config = get_llm_config()
            if not config:
                return self._heuristic_analyze(item)
            messages = [
                {"role": "system", "content": (
                    "你是加密货币新闻分析专家。分析以下新闻对市场的影响。\n"
                    "返回严格JSON:\n"
                    '{"direction": 0.5, "strength": 3, "duration": "short", '
                    '"symbols": ["BTC"], "category": "regulation", '
                    '"confidence": 0.8, "summary": "一句话摘要"}\n'
                    "direction: -1.0(极度利空)~+1.0(极度利多)\n"
                    "strength: 1(微弱)~5(极端)\n"
                    "duration: short(<24h) / medium(1-7d) / long(>7d)\n"
                    "category: regulation/exchange/tech/macro/whale/blackswan/general"
                )},
                {"role": "user", "content": f"新闻标题: {item['title']}\n来源: {item['source']}"},
            ]
            resp = call_llm_api_sync(config, messages=messages)
            content = resp["choices"][0]["message"]["content"]
            import json
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                return NewsImpact(
                    direction=max(-1, min(1, float(parsed.get("direction", 0)))),
                    strength=max(1, min(5, int(parsed.get("strength", 1)))),
                    duration=parsed.get("duration", "short"),
                    symbols=parsed.get("symbols", ["BTC"]),
                    category=parsed.get("category", "general"),
                    confidence=max(0, min(1, float(parsed.get("confidence", 0.5)))),
                    summary=parsed.get("summary", item["title"][:100]),
                )
        except Exception as e:
            logger.warning(f"[NewsIntel] LLM分析失败，使用启发式: {e}")

        return self._heuristic_analyze(item)

    def _heuristic_analyze(self, item: Dict) -> NewsImpact:
        """当LLM不可用时的关键词启发式分析"""
        title = item["title"].lower()
        impact = NewsImpact(summary=item["title"][:100])

        negative_kw = ["ban", "hack", "exploit", "sec", "lawsuit", "crash", "fraud", "scam"]
        positive_kw = ["approval", "etf", "adoption", "partnership", "bullish", "record", "launch"]

        neg_count = sum(1 for kw in negative_kw if kw in title)
        pos_count = sum(1 for kw in positive_kw if kw in title)

        if neg_count > pos_count:
            impact.direction = -0.3 * neg_count
            impact.category = "regulation"
        elif pos_count > neg_count:
            impact.direction = 0.3 * pos_count
            impact.category = "general"

        impact.direction = max(-1, min(1, impact.direction))
        impact.confidence = 0.3
        return impact

    # ────────────────────────── DB ──────────────────────────

    def _save_to_db(self, db: Session, item: Dict, impact: NewsImpact) -> Dict:
        from backend.database.models import NewsEvent
        event = NewsEvent(
            source=item.get("source", "unknown"),
            title=item.get("title", ""),
            url=item.get("url"),
            published_at=(
                datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
                if item.get("published_at") and isinstance(item["published_at"], str)
                and _try_parse_pub(item["published_at"])
                else None
            ),
            impact_direction=impact.direction,
            impact_strength=impact.strength,
            impact_duration=impact.duration,
            affected_symbols=impact.symbols,
            event_category=impact.category,
            confidence=impact.confidence,
            ai_summary=impact.summary,
            raw_data=item,
        )
        # [2026-08-15 P0-1 修复] NewsEvent 是 MarketBase 模型，必须写 Market DB
        # （alpha_market）。此前调用方传入核心库 SessionLocal → commit 静默失败被吞，
        # 导致 news_events 长期 0 行而日志谎报「分析了 N 条」。此处改用
        # MarketSessionLocal；commit 失败显式告警，不再静默吞错。
        session_factory = None
        if db is not None and getattr(db, "bind", None) is not None:
            try:
                if "alpha_market" in str(db.bind.url):
                    session_factory = lambda: db  # noqa: E731
            except Exception:
                pass
        if session_factory is None:
            from backend.database.connection import MarketSessionLocal
            session_factory = MarketSessionLocal
        _s = session_factory()
        try:
            try:
                _s.add(event)
                _s.commit()
            except Exception as e:
                _s.rollback()
                logger.error(f"[NewsIntel] 新闻落库失败（Market DB）: {e}")
        finally:
            if _s is not db:
                _s.close()
        return {
            "title": event.title,
            "source": event.source,
            "direction": event.impact_direction,
            "strength": event.impact_strength,
            "category": event.event_category,
            "summary": event.ai_summary,
        }


news_intelligence = NewsIntelligenceService()
