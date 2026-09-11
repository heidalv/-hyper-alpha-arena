# -*- coding: utf-8 -*-
"""交易所公告采集（免费公开接口）→ exchange_announcements + market_events。

数据源：
  Binance CMS  https://www.binance.com/bapi/composite/v1/public/cms/article/list/query
               catalogId=48（New Cryptocurrency Listing：现货/合约上新、HODLer 空投、Launchpool）
               catalogId=161（Delisting：下架/移除交易对）
  OKX          https://www.okx.com/api/v5/support/announcements?annType=announcements-new-listings|announcements-delistings
  Bybit        https://api.bybit.com/v5/announcements/index?type=new_crypto|delistings

分类是纯函数（`classify_title`），便于单测；币种提取 `extract_symbols` 同样纯函数。
写入幂等：dedupe_hash = sha1(exchange|source_id 或 url 或 title)。

事件严重度：下架 4、监控标签 3、上新/合约上新 3、Launchpool/空投 2、维护 1、其它 1。
方向：下架/监控 -1/-0.5；上新 +0.5；空投/Launchpool +0.3；其它 NULL。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.services.events import market_events_store as mes
from backend.services.events._http import get_json

logger = logging.getLogger(__name__)

_schema_ready = False
_schema_lock = threading.Lock()

BINANCE_CMS_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
BINANCE_CATALOGS: Dict[int, str] = {48: "listing", 161: "delisting"}
BINANCE_ARTICLE_URL = "https://www.binance.com/en/support/announcement/{code}"
OKX_URL = "https://www.okx.com/api/v5/support/announcements"
OKX_TYPES: Dict[str, str] = {"announcements-new-listings": "listing", "announcements-delistings": "delisting"}
BYBIT_URL = "https://api.bybit.com/v5/announcements/index"
BYBIT_TYPES: Dict[str, str] = {"new_crypto": "listing", "delistings": "delisting"}

# 全大写 token 中不是币的常见词（币种提取停用表）
_STOP = {
    "USDT", "USDC", "BUSD", "FDUSD", "USD", "USDE", "USDS", "USDⓈ", "USDⓈ-M", "COIN", "COIN-M", "USDS-M",
    "THE", "AND", "FOR", "WITH", "NEW", "ADD", "ADDS", "LIST", "LISTS", "WILL", "PERPETUAL", "CONTRACT", "CONTRACTS",
    "AMA", "API", "APP", "P2P", "VIP", "OTC", "ETF", "CEO", "UTC", "AM", "PM", "EST", "DEX", "CEX", "TGE", "IEO",
    "ICO", "IDO", "NFT", "RWA", "AI", "DEFI", "FAQ", "II", "III", "IV", "TBA", "TBD", "OKX", "BYBIT", "BINANCE",
    "HODLER", "HODLERS", "LAUNCHPOOL", "LAUNCHPAD", "MARGIN", "SPOT", "FUTURES", "EARN", "ALPHA", "PRE", "MARKET",
    "BUY", "SELL", "CRYPTO", "TOKEN", "TOKENS", "TRADING", "PAIRS", "PAIR", "BOTS", "BOT", "SIMPLE", "FLEXIBLE",
    "LOCKED", "PRODUCTS", "ON", "OF", "TO", "IN", "AT", "BY", "US", "EU", "UK", "HK", "SG", "JP", "KR", "NOTICE",
    "UPDATE", "UPDATES", "DELIST", "DELISTS", "DELISTING", "REMOVE", "REMOVAL", "SUPPORT", "SUPPORTS", "NETWORK",
    "UPGRADE", "HARD", "FORK", "WALLET", "MAINTENANCE", "SUSPEND", "SUSPENSION", "DEPOSIT", "DEPOSITS", "WITHDRAWAL",
    "WITHDRAWALS", "AIRDROP", "AIRDROPS", "PORTAL", "SEED", "TAG", "MONITORING", "ZONE", "INNOVATION", "OPTIONS",
    "OPTION", "INDEX", "PRICE", "MARK", "ISO", "RFQ", "APR", "APY", "ROI", "PNL", "USA", "CFD", "CFTC", "SEC",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2", "T", "M", "X", "EQUITIES", "EQUITY", "STOCK", "STOCKS", "BSTOCKS",
    "COLLATERAL", "ASSET", "ASSETS", "SECURITIES", "TOKENIZED",
}
_PAREN_RE = re.compile(r"\(([A-Z0-9]{2,12})\)")
_PAIR_RE = re.compile(r"\b([A-Z0-9]{2,12})(?:USDT|USDC|USD|BUSD|FDUSD)\b")
_CAPS_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})\b")


@dataclass
class Announcement:
    exchange: str
    ann_type: str           # listing / futures_listing / delisting / monitoring_tag / airdrop / launchpool / maintenance / other
    title: str
    url: str
    published_at_ms: int
    source_id: str = ""
    symbols: List[str] = field(default_factory=list)
    effective_at_ms: Optional[int] = None
    severity: int = 1
    direction: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    dedupe_hash: str = ""

    def finalize(self) -> "Announcement":
        if not self.dedupe_hash:
            key = self.source_id or self.url or self.title
            self.dedupe_hash = mes.make_dedupe_hash("ann", self.exchange, key)
        self.title = (self.title or "").strip()[:500]
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：分类 / 币种提取
# ─────────────────────────────────────────────────────────────────────────────
def classify_title(title: str, hint: str = "") -> Tuple[str, int, Optional[float]]:
    """标题（+目录提示）→ (ann_type, severity, direction)。

    hint: 来源目录语义（listing / delisting / ""），只在标题无明确关键词时兜底。
    """
    t = (title or "").lower()
    # 抵押/借贷/杠杆资产范围调整：不是交易下架，单列一类（严重度 2），避免误触 72h 禁开
    if ("collateral" in t or "lending" in t or "loanable" in t or "borrow" in t or "margin asset" in t) and "delist" not in t:
        negative = any(k in t for k in ("discontinu", "remov", "cease", "terminat", "suspend", "delist"))
        return "collateral", 2, (-0.3 if negative else 0.2)
    if any(k in t for k in ("delist", "will remove", "removal of", "cease trading", "terminate", "termination of",
                            "will close", "closing of", "discontinue")):
        return "delisting", 4, -1.0
    if "monitoring tag" in t or "seed tag" in t or "observation zone" in t or "monitoring zone" in t:
        return "monitoring_tag", 3, -0.5
    if "hodler airdrop" in t or "airdrop" in t:
        return "airdrop", 2, 0.3
    if "launchpool" in t or "launchpad" in t or "megadrop" in t or "jumpstart" in t:
        return "launchpool", 2, 0.3
    if ("perpetual" in t or "futures" in t or "usdⓈ-m" in t or "usds-m" in t or "coin-m" in t) and (
        "launch" in t or "list" in t or "add" in t or "introduc" in t
    ):
        return "futures_listing", 3, 0.5
    if any(k in t for k in ("will list", "lists ", "listing", "new listing", "to list", "will add", "adds ",
                            "trading pairs", "available for trading", "introduc")):
        return "listing", 3, 0.5
    if any(k in t for k in ("maintenance", "suspend", "suspension", "network upgrade", "hard fork", "wallet")):
        return "maintenance", 1, 0.0
    if hint == "delisting":
        return "delisting", 4, -1.0
    if hint == "listing":
        return "listing", 2, 0.3
    return "other", 1, None


def extract_symbols(title: str, *, max_symbols: int = 8) -> List[str]:
    """从标题提取币种（优先括号 (FOO)、其次 FOOUSDT、最后全大写 token 兜底）。"""
    if not title:
        return []
    out: List[str] = []
    seen = set()

    def _add(sym: str) -> None:
        s = sym.strip().upper()
        if not s or s in _STOP or s in seen or s.isdigit() or len(s) < 2:
            return
        # 至少两个字母（"1000PEPE" 合法；"2026Q3" 之类编号剔除）
        if sum(1 for c in s if c.isalpha()) < 2:
            return
        seen.add(s)
        out.append(s)

    for m in _PAREN_RE.findall(title):
        _add(m)
    for m in _PAIR_RE.findall(title):
        _add(m)
    if not out:
        # 兜底：全大写 token；为减少误报，要求至少一个字母且长度 2..8
        for m in _CAPS_RE.findall(title):
            if 2 <= len(m) <= 8 and any(c.isalpha() for c in m):
                _add(m)
    return out[:max_symbols]


# ─────────────────────────────────────────────────────────────────────────────
# 各源解析（纯函数，输入 JSON → Announcement 列表）
# ─────────────────────────────────────────────────────────────────────────────
def parse_binance(payload: Any, catalog_id: int) -> List[Announcement]:
    hint = BINANCE_CATALOGS.get(catalog_id, "")
    out: List[Announcement] = []
    try:
        catalogs = (payload or {}).get("data", {}).get("catalogs", []) or []
    except AttributeError:
        return out
    for cat in catalogs:
        for a in cat.get("articles", []) or []:
            title = str(a.get("title") or "").strip()
            if not title:
                continue
            code = str(a.get("code") or "")
            ts = a.get("releaseDate")
            try:
                ts_ms = int(ts)
            except (TypeError, ValueError):
                continue
            ann_type, sev, direction = classify_title(title, hint)
            out.append(Announcement(
                exchange="binance", ann_type=ann_type, title=title,
                url=BINANCE_ARTICLE_URL.format(code=code) if code else "",
                published_at_ms=ts_ms, source_id=f"binance:{a.get('id') or code}",
                symbols=extract_symbols(title), severity=sev, direction=direction,
                raw={"id": a.get("id"), "code": code, "catalogId": catalog_id, "type": a.get("type")},
            ).finalize())
    return out


def parse_okx(payload: Any, ann_type_key: str) -> List[Announcement]:
    hint = OKX_TYPES.get(ann_type_key, "")
    out: List[Announcement] = []
    try:
        data = (payload or {}).get("data", []) or []
    except AttributeError:
        return out
    for block in data:
        for d in block.get("details", []) or []:
            title = str(d.get("title") or "").strip()
            if not title:
                continue
            try:
                ts_ms = int(d.get("pTime") or 0)
            except (TypeError, ValueError):
                continue
            if ts_ms <= 0:
                continue
            eff = d.get("businessPTime")
            try:
                eff_ms = int(eff) if eff else None
            except (TypeError, ValueError):
                eff_ms = None
            ann_type, sev, direction = classify_title(title, hint)
            url = str(d.get("url") or "")
            out.append(Announcement(
                exchange="okx", ann_type=ann_type, title=title, url=url, published_at_ms=ts_ms,
                source_id=f"okx:{url or title}", symbols=extract_symbols(title),
                effective_at_ms=eff_ms, severity=sev, direction=direction,
                raw={"annType": d.get("annType"), "businessPTime": eff},
            ).finalize())
    return out


def parse_bybit(payload: Any, type_key: str) -> List[Announcement]:
    hint = BYBIT_TYPES.get(type_key, "")
    out: List[Announcement] = []
    try:
        items = (payload or {}).get("result", {}).get("list", []) or []
    except AttributeError:
        return out
    for it in items:
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        ts = it.get("publishTime") or it.get("dateTimestamp")
        try:
            ts_ms = int(ts)
        except (TypeError, ValueError):
            continue
        tags = [str(x) for x in (it.get("tags") or [])]
        ann_type, sev, direction = classify_title(title, hint)
        if ann_type == "listing" and any("derivativ" in x.lower() or "futures" in x.lower() or "perpetual" in x.lower() for x in tags):
            ann_type, sev, direction = "futures_listing", 3, 0.5
        url = str(it.get("url") or "")
        out.append(Announcement(
            exchange="bybit", ann_type=ann_type, title=title, url=url, published_at_ms=ts_ms,
            source_id=f"bybit:{url or title}", symbols=extract_symbols(title),
            effective_at_ms=(int(it["startDateTimestamp"]) if it.get("startDateTimestamp") else None),
            severity=sev, direction=direction,
            raw={"type": (it.get("type") or {}).get("key"), "tags": tags, "description": str(it.get("description") or "")[:300]},
        ).finalize())
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 抓取
# ─────────────────────────────────────────────────────────────────────────────
def fetch_all(page_size: int = 20) -> Tuple[List[Announcement], Dict[str, Any]]:
    """抓取全部源，返回 (公告列表, 逐源诊断)。任何源失败 → 该源 status=error，不影响其它源。"""
    anns: List[Announcement] = []
    diag: Dict[str, Any] = {}
    for cid in BINANCE_CATALOGS:
        key = f"binance:{cid}"
        t0 = time.time()
        payload = get_json(BINANCE_CMS_URL, {"type": 1, "pageNo": 1, "pageSize": page_size, "catalogId": cid}, timeout=20)
        if payload is None:
            diag[key] = {"status": "error", "count": 0, "elapsed_ms": int((time.time() - t0) * 1000)}
            continue
        rows = parse_binance(payload, cid)
        anns.extend(rows)
        diag[key] = {"status": "ok", "count": len(rows), "elapsed_ms": int((time.time() - t0) * 1000)}
    for at in OKX_TYPES:
        key = f"okx:{at}"
        t0 = time.time()
        payload = get_json(OKX_URL, {"annType": at}, timeout=20)
        if payload is None:
            diag[key] = {"status": "error", "count": 0, "elapsed_ms": int((time.time() - t0) * 1000)}
            continue
        rows = parse_okx(payload, at)
        anns.extend(rows)
        diag[key] = {"status": "ok", "count": len(rows), "elapsed_ms": int((time.time() - t0) * 1000)}
    for bt in BYBIT_TYPES:
        key = f"bybit:{bt}"
        t0 = time.time()
        payload = get_json(BYBIT_URL, {"locale": "en-US", "type": bt, "limit": min(50, max(5, page_size))}, timeout=20)
        if payload is None:
            diag[key] = {"status": "error", "count": 0, "elapsed_ms": int((time.time() - t0) * 1000)}
            continue
        rows = parse_bybit(payload, bt)
        anns.extend(rows)
        diag[key] = {"status": "ok", "count": len(rows), "elapsed_ms": int((time.time() - t0) * 1000)}
    return anns, diag


# ─────────────────────────────────────────────────────────────────────────────
# 落库
# ─────────────────────────────────────────────────────────────────────────────
def _db():
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            from sqlalchemy import text
            db = _db()
            try:
                db.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS exchange_announcements (
                        id BIGSERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL,
                        ann_type VARCHAR(32) NOT NULL,
                        title TEXT NOT NULL,
                        url TEXT,
                        symbols JSONB,
                        published_at_ms BIGINT NOT NULL,
                        effective_at_ms BIGINT,
                        severity SMALLINT NOT NULL DEFAULT 1,
                        direction REAL,
                        source_id VARCHAR(200),
                        raw JSONB,
                        dedupe_hash VARCHAR(64) NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_exch_ann_pub ON exchange_announcements (exchange, published_at_ms DESC)"
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_exch_ann_type_pub ON exchange_announcements (ann_type, published_at_ms DESC)"
                ))
                db.commit()
                _schema_ready = True
            except Exception as exc:
                db.rollback()
                logger.warning("[announcements] 建表失败: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[announcements] 建表跳过: %s", exc)


def persist(anns: Iterable[Announcement]) -> List[Announcement]:
    """写 exchange_announcements（ON CONFLICT DO NOTHING），返回**新插入**的公告。"""
    rows = [a.finalize() for a in anns]
    if not rows:
        return []
    ensure_schema()
    new: List[Announcement] = []
    try:
        from sqlalchemy import text
        db = _db()
        try:
            stmt = text(
                "INSERT INTO exchange_announcements (exchange, ann_type, title, url, symbols, published_at_ms, effective_at_ms, "
                "severity, direction, source_id, raw, dedupe_hash) VALUES (:exchange, :ann_type, :title, :url, CAST(:symbols AS JSONB), "
                ":published_at_ms, :effective_at_ms, :severity, :direction, :source_id, CAST(:raw AS JSONB), :dedupe_hash) "
                "ON CONFLICT (dedupe_hash) DO NOTHING"
            )
            for a in rows:
                res = db.execute(stmt, {
                    "exchange": a.exchange, "ann_type": a.ann_type, "title": a.title, "url": a.url or None,
                    "symbols": json.dumps(a.symbols), "published_at_ms": int(a.published_at_ms),
                    "effective_at_ms": a.effective_at_ms, "severity": int(a.severity), "direction": a.direction,
                    "source_id": (a.source_id or "")[:200] or None, "raw": json.dumps(a.raw, ensure_ascii=False, default=str),
                    "dedupe_hash": a.dedupe_hash,
                })
                if int(res.rowcount or 0) > 0:
                    new.append(a)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("[announcements] 写入失败: %s", exc)
            return []
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[announcements] 写入跳过: %s", exc)
        return []
    return new


def to_market_events(anns: Iterable[Announcement]) -> List[mes.MarketEvent]:
    """公告 → 事件总线（每个币一条；无币则一条全市场事件）。"""
    type_map = {
        "listing": mes.ANNOUNCEMENT_LISTING,
        "futures_listing": mes.ANNOUNCEMENT_FUTURES_LISTING,
        "delisting": mes.ANNOUNCEMENT_DELISTING,
        "monitoring_tag": mes.ANNOUNCEMENT_MONITORING_TAG,
        "airdrop": mes.ANNOUNCEMENT_AIRDROP,
        "launchpool": mes.ANNOUNCEMENT_LAUNCHPOOL,
        "maintenance": mes.ANNOUNCEMENT_MAINTENANCE,
        "collateral": mes.ANNOUNCEMENT_COLLATERAL,
        "other": mes.ANNOUNCEMENT_OTHER,
    }
    out: List[mes.MarketEvent] = []
    for a in anns:
        et = type_map.get(a.ann_type, mes.ANNOUNCEMENT_OTHER)
        syms = a.symbols or [None]
        for s in syms:
            out.append(mes.MarketEvent(
                event_type=et, ts_ms=int(a.published_at_ms), source=f"{a.exchange}_ann",
                symbol=s, severity=a.severity, direction=a.direction, title=a.title,
                payload={"url": a.url, "exchange": a.exchange, "ann_type": a.ann_type,
                         "symbols": a.symbols, "effective_at_ms": a.effective_at_ms},
                dedupe_hash=mes.make_dedupe_hash("ann_evt", a.dedupe_hash, s or ""),
            ))
    return out


_LAST: Dict[str, Any] = {}


def collect_once() -> Dict[str, Any]:
    """一轮采集：抓取 → 落库（幂等）→ 新公告写事件总线。返回摘要（供 job_registry/接口）。"""
    t0 = time.time()
    anns, diag = fetch_all()
    new = persist(anns)
    events_written = mes.publish(to_market_events(new)) if new else 0
    summary = {
        "fetched": len(anns), "new": len(new), "events_written": events_written,
        "sources": diag, "offline": all(d.get("status") != "ok" for d in diag.values()) if diag else True,
        "elapsed_ms": int((time.time() - t0) * 1000), "as_of": int(time.time() * 1000),
        "new_titles": [f"{a.exchange}:{a.ann_type}:{a.title[:80]}" for a in new[:10]],
    }
    _LAST.clear()
    _LAST.update(summary)
    if new:
        logger.info("[announcements] 新公告 %d 条（事件 %d）：%s", len(new), events_written,
                    "; ".join(summary["new_titles"])[:400])
    else:
        logger.info("[announcements] 本轮无新公告（抓取 %d，%s）", len(anns),
                    " ".join(f"{k}={v.get('status')}" for k, v in diag.items()))
    return summary


def last_summary() -> Dict[str, Any]:
    return dict(_LAST)
