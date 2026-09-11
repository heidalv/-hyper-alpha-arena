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


def parse_pub_time(raw: Any) -> Optional[datetime]:
    """RSS / CryptoPanic 发布时间 → aware datetime；解析不出返回 None。

    [2026-09-03 修复] 原实现只试 `datetime.fromisoformat`，而 RSS(theblock/decrypt/cointelegraph)
    给的是 **RFC-2822**（`'Fri, 14 Aug 2026 21:19:39 +0000'`），fromisoformat 一律抛错 →
    `published_at` 落库全是 NULL（实测 5040 行无一有值），新闻事件因此没有时间轴，
    事件研究与 E5-5 全都无法对齐 K 线。这里按 RFC-2822 → ISO 顺序解析。
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _try_parse_pub(raw: str) -> bool:
    """兼容旧调用点：是否可解析。"""
    return parse_pub_time(raw) is not None


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
    # [2026-09-08 修复] coindesk 原 URL 带尾斜杠会返回 308 跳转，httpx 默认不跟随
    # → 该源自建库以来 0 条入库。直接使用跳转后的目标地址（并全局开启 follow_redirects）。
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("decrypt", "https://decrypt.co/feed"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    # [2026-09-08] CoinJournal：此前被 news_feed.py 单独抓取只喂旧 prompt、从不入
    # news_events 库（双轨浪费）。并入主采集器统一去重+标注；实测代理/直连均 200。
    ("coinjournal", "https://coinjournal.net/news/feed/"),
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
        self._blockbeats_key = self._load_key("BLOCKBEATS_API_KEY")
        self._seen_hashes: set = set()
        self._latest_events: List[Dict] = []
        self._latest_ts: float = 0
        # [2026-09-08] CryptoPanic 被 Cloudflare 403 封锁（两个本地代理均实测如此）。
        # 命中 403 后进入冷却期，期间不再请求，避免每 90s 刷一次无效错误。
        self._cp_blocked_until: float = 0.0
        logger.info(
            f"[NewsIntel] 新闻情报服务初始化完成 "
            f"(CryptoPanic={'有Key' if self._cryptopanic_key else '无Key'}, "
            f"BlockBeats={'有Key' if self._blockbeats_key else '无Key'})"
        )

    @staticmethod
    def _load_key(env_name: str) -> str:
        """环境变量 → SystemConfig 表 两级读取 API Key。"""
        key = os.environ.get(env_name, "")
        if key:
            return key
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import SystemConfig
            db = SessionLocal()
            try:
                cfg = db.query(SystemConfig).filter(SystemConfig.key == env_name).first()
                if cfg and cfg.value:
                    os.environ[env_name] = cfg.value
                    return cfg.value
            finally:
                db.close()
        except Exception:
            pass
        return ""

    @classmethod
    def _load_cryptopanic_key(cls) -> str:
        return cls._load_key("CRYPTOPANIC_API_KEY")

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
                # 封锁冷却期内暂停轮询（每小时重试一次探测解封）
                if time.time() < self._cp_blocked_until:
                    time.sleep(3600.0)
                    continue
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
        if time.time() < self._cp_blocked_until:
            return
        proxy = os.environ.get("BINANCE_HTTPS_PROXY") or None
        try:
            async with httpx.AsyncClient(timeout=10, proxy=proxy, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=15, proxy=proxy, follow_redirects=True) as client:
            tasks = []
            if self._cryptopanic_key and time.time() >= self._cp_blocked_until:
                tasks.append(self._fetch_cryptopanic(client))
            tasks.append(self._fetch_rss(client))
            if self._blockbeats_key:
                tasks.append(self._fetch_blockbeats(client))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    items.extend(r)
        return items

    async def _fetch_blockbeats(self, client: httpx.AsyncClient) -> List[Dict]:
        """BlockBeats Pro API 中文快讯（[2026-09-08] 新增中文源）。

        官方 Pro API：GET https://api-pro.theblockbeats.info/v1/newsflash，
        请求头 api-key。免费档在 https://www.theblockbeats.info/ 申请。
        旧的无 Key 开放接口（api.theblockbeats.news/v1/open-api/open-flash）
        已实测废弃（status=0 但永远返回空数组），不要回退到它。
        """
        items: List[Dict] = []
        try:
            r = await client.get(
                "https://api-pro.theblockbeats.info/v1/newsflash",
                params={"size": "20", "page": "1", "lang": "cn"},
                headers={"api-key": self._blockbeats_key, "Accept": "application/json"},
            )
            if r.status_code != 200:
                logger.debug(f"[NewsIntel] BlockBeats 响应 {r.status_code}")
                return []
            data = r.json()
            if int(data.get("status", -1)) != 0:
                logger.debug(f"[NewsIntel] BlockBeats status={data.get('status')} {data.get('message')}")
                return []
            payload = data.get("data")
            rows = payload.get("data") if isinstance(payload, dict) else payload
            for it in rows or []:
                title = (it.get("title") or it.get("content") or "").strip()
                if not title:
                    continue
                # create_time 两种形态：旧开放接口是 epoch 秒字符串；Pro API 是
                # "2026-09-08 18:19:04" 北京时间字符串（实测）。统一转 aware UTC ISO，
                # 交给 parse_pub_time 落库，避免被当 UTC 导致快讯"穿越到 8 小时后"。
                pub = ""
                ct = str(it.get("create_time") or "").strip()
                try:
                    if ct.isdigit():
                        pub = datetime.fromtimestamp(int(ct), tz=timezone.utc).isoformat()
                    elif ct:
                        dt = datetime.strptime(ct[:19], "%Y-%m-%d %H:%M:%S").replace(
                            tzinfo=timezone(timedelta(hours=8)))
                        pub = dt.isoformat()
                except Exception:
                    pub = ""
                items.append({
                    "source": "blockbeats",
                    "title": title[:300],
                    "url": it.get("link") or it.get("url") or "",
                    "published_at": pub,
                    "currencies": [],
                    "votes": {},
                })
        except Exception as e:
            logger.debug(f"[NewsIntel] BlockBeats 获取失败: {e}")
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
                # [2026-09-08] 403 = Cloudflare 封锁（返回挑战页 HTML 而非 JSON）。
                # 进入 6 小时冷却，避免每 90s/5min 持续打被封的端点。
                if r.status_code in (401, 403):
                    if time.time() >= self._cp_blocked_until:
                        logger.warning(
                            "[NewsIntel] CryptoPanic 返回 %s（疑似 Cloudflare 封锁），冷却 6 小时",
                            r.status_code,
                        )
                    self._cp_blocked_until = time.time() + 6 * 3600
                else:
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
        direct_client: Optional[httpx.AsyncClient] = None
        try:
            for name, url in RSS_FEEDS:
                try:
                    r = await client.get(url)
                    # [2026-09-08 修复] TheBlock 等源被本地代理出口 IP 403（Cloudflare），
                    # 但直连正常。代理拿到 401/403/451 时用直连客户端兜底重试一次。
                    if r.status_code in (401, 403, 451):
                        try:
                            if direct_client is None:
                                direct_client = httpx.AsyncClient(timeout=15, follow_redirects=True)
                            r2 = await direct_client.get(url)
                            if r2.status_code == 200:
                                r = r2
                        except Exception:
                            pass
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
        finally:
            if direct_client is not None:
                try:
                    await direct_client.aclose()
                except Exception:
                    pass
        return items

    # ────────────────────────── dedup ──────────────────────────

    @staticmethod
    def _dedup_key(item: Dict) -> str:
        """去重键：URL 优先，缺失时回落标题。

        实测近 20 天的 336 组重复**全部**是同一 URL 被反复插入（而非不同媒体
        报道同一事件），故 URL 是可靠的同一性判据。
        """
        return (item.get("url") or "").strip() or (item.get("title") or "").strip()

    def _existing_keys(self, items: List[Dict]) -> set:
        """批量查库，返回已入库的去重键集合。一次 IN 查询，不按条 N+1。"""
        keys = [k for k in (self._dedup_key(i) for i in items) if k]
        if not keys:
            return set()
        try:
            from sqlalchemy import text

            from backend.database.connection import MarketSessionLocal
            db = MarketSessionLocal()
            try:
                rows = db.execute(text(
                    "SELECT COALESCE(NULLIF(TRIM(url), ''), TRIM(title)) AS k FROM news_events "
                    "WHERE COALESCE(NULLIF(TRIM(url), ''), TRIM(title)) = ANY(:keys)"
                ), {"keys": keys}).all()
                return {r[0] for r in rows if r[0]}
            finally:
                db.close()
        except Exception as exc:
            # 查不通时**不**放行：宁可这一轮少收几条，也好过再写出成百上千条重复。
            logger.warning("[NewsIntel] 去重查库失败，本轮跳过入库: %s", exc)
            return {self._dedup_key(i) for i in items}

    def _deduplicate(self, items: List[Dict]) -> List[Dict]:
        """内存 + 数据库两级去重。

        [2026-09-04] 原实现只有内存 `_seen_hashes`，两个后果：
          1. **进程一重启集合就清空**，而 RSS 源里还是那批新闻 → 整批重新入库；
          2. 裁剪写的是 `set(list(self._seen_hashes)[-2500:])`，而 set 无序，
             取到的是任意 2500 个而非最近的，等于随机遗忘一半。
        落库侧又没有任何唯一约束，于是同一条新闻最多被插了 **79 次**，
        全表 5271 行里只有 632 条是真新闻（重复率 88%），
        所有基于 news_events 的统计因此被放大约 8 倍。
        已清理历史重复并加唯一索引 ux_news_events_url / ux_news_events_title。

        数据库这一级是权威：它不随进程重启失效。内存这级保留，用于在同一轮内
        快速滤掉多源重复，省一次查库。
        """
        fresh: List[Dict] = []
        for item in items:
            h = hashlib.md5((self._dedup_key(item) or "").encode()).hexdigest()
            if h in self._seen_hashes:
                continue
            self._seen_hashes.add(h)
            fresh.append(item)
        # 内存集合只是加速层，超限后清空即可 —— 权威判据在库里，清空不会导致重复入库。
        if len(self._seen_hashes) > 5000:
            self._seen_hashes.clear()
        if not fresh:
            return []

        known = self._existing_keys(fresh)
        unique = [i for i in fresh if self._dedup_key(i) not in known]
        if len(unique) < len(fresh):
            logger.info("[NewsIntel] 去重：%d 条中 %d 条已入库，本轮新增 %d 条",
                        len(fresh), len(fresh) - len(unique), len(unique))
        return unique

    # ────────────────────────── LLM分析 ──────────────────────────

    # 标注用的系统提示。与 schemas.py 的 news_annotate 契约同源，量纲必须一致。
    _ANNOTATE_SYSTEM = (
        "你是加密货币新闻分析专家。判断新闻对市场的实际影响，而不是复述标题。\n"
        "direction: -1.0(极度利空)~+1.0(极度利多)，无明确方向给 0\n"
        "strength: 1(微弱)~5(极端)。日常公告/融资/合作通常 1-2；"
        "交易所被盗、监管禁令、ETF 获批这类才给 4-5\n"
        "duration: short(<24h) / medium(1-7d) / long(>7d)\n"
        "category: regulation/exchange/tech/macro/whale/blackswan/general\n"
        "symbols: 受影响的币种代号列表（全市场影响填 [\"BTC\"]）"
    )

    def _annotate_via_gateway(self, item: Dict) -> Optional["NewsImpact"]:
        """系统级网关标注（MiniMax / GLM / 本地票）。

        [2026-09-04] 原实现只走 `get_llm_config()`，而该函数**要求 tenant_id**，
        新闻服务作为系统级采集器并没有租户上下文 —— 于是每条新闻都拿到 None，
        静默回落关键词启发式。后果不是"标注差一点"，而是整条因果链断掉：
        库里 3790/3809 条标注都是词表打的（confidence 恒 0.3），
        强度/方向没有语义依据 → e5_5_news_hedge 在 7 组阈值 × 4 个时间窗上
        超额全为负、无一过成本线（14bp），策略永远无法晋升。
        放宽阈值救不了：越放宽越趋近随机（命中率 0.50、超额 −2.4bp），
        因为缺的是语义理解，不是样本量。
        """
        try:
            from backend.services.analysis import schemas
            from backend.services.analysis.model_gateway import get_model_gateway

            gw = get_model_gateway()
            user = f"新闻标题: {item.get('title', '')}\n来源: {item.get('source', '')}"
            # 单模型即可：标注是客观信息抽取，不像策略判断那样需要交叉验证。
            # 顺序按实测定：MiniMax 4.5s、GLM 17~26s，两者标注质量相当（同一批样本上
            # 方向与分类一致），故快的在前；本地票兜底，免费不限量。
            for tr in ("minimax", "glm_opencode", "ollama"):
                res = gw.call("news_annotate", self._ANNOTATE_SYSTEM, user,
                              transport=tr, schema_task="news_annotate",
                              max_output_tokens=400, timeout_s=60)
                if not (res.ok and res.json):
                    continue
                ok, _errs = schemas.validate("news_annotate", res.json)
                if not ok:
                    continue
                p = res.json
                return NewsImpact(
                    direction=max(-1.0, min(1.0, float(p.get("direction", 0)))),
                    strength=max(1, min(5, int(round(float(p.get("strength", 1)))))),
                    duration=str(p.get("duration") or "short"),
                    symbols=[str(s).upper() for s in (p.get("symbols") or ["BTC"])][:6],
                    category=str(p.get("category") or "general"),
                    # 置信度如实取模型自评。库里 confidence<=0.35 被 annotation_quality()
                    # 当作"关键词标注"统计，LLM 标注必须落在其上方才能区分开。
                    confidence=max(0.4, min(1.0, float(p.get("confidence", 0.6)))),
                    summary=str(p.get("summary") or item.get("title", ""))[:200],
                )
        except Exception as exc:
            logger.debug("[NewsIntel] 网关标注失败: %s", str(exc)[:160])
        return None

    async def _analyze_with_llm(self, item: Dict) -> NewsImpact:
        try:
            from backend.services.llm_config_service import call_llm_api_sync, get_llm_config
            config = get_llm_config()
            if not config:
                # 无租户配置 → 走系统级网关；网关也不可用才退到关键词。
                impact = await asyncio.to_thread(self._annotate_via_gateway, item)
                return impact or self._heuristic_analyze(item)
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

    # 强冲击词（单个命中即可判定为高强度）与普通词，供启发式分级用
    _HEAVY_NEG = ("hack", "exploit", "ban", "bankrupt", "insolvency", "halt", "delist",
                  "liquidat", "collapse", "crash", "fraud", "indict")
    _HEAVY_POS = ("etf approval", "approves", "approval", "listing", "halving", "rate cut")
    _NEG_KW = ("ban", "hack", "exploit", "sec ", "lawsuit", "crash", "fraud", "scam", "sue",
               "probe", "subpoena", "outflow", "delist", "halt", "bankrupt", "liquidat", "exploit")
    _POS_KW = ("approval", "approves", "etf", "adoption", "partnership", "bullish", "record",
               "launch", "listing", "inflow", "upgrade", "rally")

    def _heuristic_analyze(self, item: Dict) -> NewsImpact:
        """当 LLM 不可用时的关键词启发式分析。

        [2026-09-03 修复] 原实现从不给 `strength` 赋值 → 落库 5040 行全是默认值 1，
        `impact_strength` 这一维完全没有区分度，桥接（阈值 7）与 E5-5 因此全线空转。
        现按「命中词数 + 是否强冲击词」映射到 1–5，与 LLM 分支同量纲；并从标题提取受影响币种，
        不再一律记成 BTC。摘要加 `[kw]` 前缀，下游一眼能分辨这条标注来自规则而非 LLM。
        """
        title_raw = item.get("title", "") or ""
        title = title_raw.lower()
        impact = NewsImpact(summary=f"[kw] {title_raw[:96]}")

        neg_count = sum(1 for kw in self._NEG_KW if kw in title)
        pos_count = sum(1 for kw in self._POS_KW if kw in title)
        heavy = any(kw in title for kw in self._HEAVY_NEG) or any(kw in title for kw in self._HEAVY_POS)

        if neg_count > pos_count:
            impact.direction = -0.3 * neg_count
            impact.category = "regulation" if any(k in title for k in ("sec", "lawsuit", "ban", "regulat")) else "general"
        elif pos_count > neg_count:
            impact.direction = 0.3 * pos_count
            impact.category = "general"

        impact.direction = max(-1.0, min(1.0, impact.direction))
        net = max(neg_count, pos_count)
        if net <= 0:
            impact.strength = 1
        else:
            impact.strength = max(1, min(5, net + (2 if heavy else 0)))
        impact.duration = "medium" if impact.strength >= 4 else "short"
        impact.symbols = self._extract_symbols(title_raw) or ["BTC"]
        impact.confidence = 0.3
        return impact

    _SYMBOL_HINTS = {
        "BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether ", "eth"), "SOL": ("solana", "sol "),
        "XRP": ("ripple", "xrp"), "DOGE": ("dogecoin", "doge"), "BNB": ("binance coin", "bnb"),
        "ADA": ("cardano", "ada "), "AVAX": ("avalanche", "avax"), "LINK": ("chainlink", "link "),
        "DOT": ("polkadot", "dot "), "MATIC": ("polygon", "matic"), "ARB": ("arbitrum", "arb "),
        "OP": ("optimism",), "SUI": ("sui ",), "TON": ("toncoin", "ton "),
    }

    def _extract_symbols(self, title: str) -> List[str]:
        t = f" {title.lower()} "
        out = [sym for sym, hints in self._SYMBOL_HINTS.items() if any(h in t for h in hints)]
        return out[:6]

    # ────────────────────────── DB ──────────────────────────

    def _save_to_db(self, db: Session, item: Dict, impact: NewsImpact) -> Dict:
        from backend.database.models import NewsEvent
        event = NewsEvent(
            source=item.get("source", "unknown"),
            title=item.get("title", ""),
            url=item.get("url"),
            published_at=parse_pub_time(item.get("published_at")),
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
                # [2026-09-04] 唯一索引冲突是**正常**结果，不是故障：两个采集通道
                # （90s 快通道 / 5min 全量）可能在各自查库之后、写入之前撞上同一条。
                # 索引 ux_news_events_url / ux_news_events_title 是最后一道防线，
                # 命中说明它正常工作，按 ERROR 报会淹没真正的落库故障。
                if "unique" in str(e).lower() or "duplicate key" in str(e).lower():
                    logger.debug("[NewsIntel] 新闻已存在，跳过: %s", (event.url or event.title)[:80])
                else:
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
