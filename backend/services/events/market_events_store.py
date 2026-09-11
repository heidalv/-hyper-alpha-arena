# -*- coding: utf-8 -*-
"""market_events 事件总线表（v3 p0-event-data）。

一张表承载所有"离散事件"：交易所公告、大额/级联清算、极端资金费、OI 突变、
高影响新闻、巨鲸、宏观日历。采集器只管 `publish()`，消费方（RiskEngine 事件避险窗口、
EventImpact Agent、event_study 回测）只管 `recent()` / `query()`。

表结构（alpha_market 库）：
  id            BIGSERIAL
  event_type    VARCHAR(48)   点分层级：announcement.listing / announcement.delisting /
                              liquidation.large / liquidation.cascade / funding.extreme /
                              position.oi_jump / news.high_impact / whale.large / macro.scheduled
  symbol        VARCHAR(32)   基础币（BTC）；全市场事件为 NULL
  ts_ms         BIGINT        事件发生时刻（毫秒 UTC）
  severity      SMALLINT      1..5（5 = 停机级）
  direction     REAL          -1..+1（负=利空/向下冲击；NULL=中性/未知）
  source        VARCHAR(40)   binance_cms / okx / bybit / binance_ws / news_events / whale_activities ...
  title         TEXT
  payload       JSONB         源字段原样 + 派生指标
  dedupe_hash   VARCHAR(64)   UNIQUE，采集器自行构造（sha1(source|type|symbol|自然键)）
  created_at    TIMESTAMPTZ

所有函数失败时只记日志、返回空/0，绝不向调用方抛异常（数据层不能拖垮交易主链路）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

_schema_ready = False
_schema_lock = threading.Lock()

# 事件类型常量（消费方引用常量而非字面量，避免拼写漂移）
ANNOUNCEMENT_LISTING = "announcement.listing"
ANNOUNCEMENT_FUTURES_LISTING = "announcement.futures_listing"
ANNOUNCEMENT_DELISTING = "announcement.delisting"
ANNOUNCEMENT_MONITORING_TAG = "announcement.monitoring_tag"
ANNOUNCEMENT_AIRDROP = "announcement.airdrop"
ANNOUNCEMENT_LAUNCHPOOL = "announcement.launchpool"
ANNOUNCEMENT_MAINTENANCE = "announcement.maintenance"
ANNOUNCEMENT_COLLATERAL = "announcement.collateral"
ANNOUNCEMENT_OTHER = "announcement.other"
LIQUIDATION_LARGE = "liquidation.large"
LIQUIDATION_CASCADE = "liquidation.cascade"
LIQUIDATION_MARKET_CASCADE = "liquidation.market_cascade"
FUNDING_EXTREME = "funding.extreme"
POSITION_OI_JUMP = "position.oi_jump"
NEWS_HIGH_IMPACT = "news.high_impact"
WHALE_LARGE = "whale.large"
MACRO_SCHEDULED = "macro.scheduled"
MACRO_RELEASED = "macro.released"
SMART_MONEY_MOVE = "smart_money.move"  # [2026-09-08] 聪明钱（OKX带单员/HL鲸鱼）仓位动作

ALL_EVENT_TYPES = (
    ANNOUNCEMENT_LISTING, ANNOUNCEMENT_FUTURES_LISTING, ANNOUNCEMENT_DELISTING,
    ANNOUNCEMENT_MONITORING_TAG, ANNOUNCEMENT_AIRDROP, ANNOUNCEMENT_LAUNCHPOOL,
    ANNOUNCEMENT_MAINTENANCE, ANNOUNCEMENT_COLLATERAL, ANNOUNCEMENT_OTHER,
    LIQUIDATION_LARGE, LIQUIDATION_CASCADE, LIQUIDATION_MARKET_CASCADE,
    FUNDING_EXTREME, POSITION_OI_JUMP, NEWS_HIGH_IMPACT, WHALE_LARGE,
    MACRO_SCHEDULED, MACRO_RELEASED, SMART_MONEY_MOVE,
)


@dataclass
class MarketEvent:
    event_type: str
    ts_ms: int
    source: str
    symbol: Optional[str] = None
    severity: int = 1
    direction: Optional[float] = None
    title: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    dedupe_hash: str = ""

    def finalize(self) -> "MarketEvent":
        """补齐 dedupe_hash（未显式给出时按 source|type|symbol|ts 构造）并夹紧字段范围。"""
        if self.symbol:
            self.symbol = str(self.symbol).strip().upper()[:32] or None
        if not self.dedupe_hash:
            self.dedupe_hash = make_dedupe_hash(self.source, self.event_type, self.symbol or "", self.ts_ms)
        self.severity = int(max(1, min(5, int(self.severity or 1))))
        if self.direction is not None:
            try:
                self.direction = float(max(-1.0, min(1.0, float(self.direction))))
            except (TypeError, ValueError):
                self.direction = None
        self.title = (self.title or "")[:500]
        return self

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_dedupe_hash(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def _db():
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def ensure_schema() -> None:
    """建表（幂等）。alpha_market 库；无 RLS（纯公共行情事件）。"""
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
                    CREATE TABLE IF NOT EXISTS market_events (
                        id BIGSERIAL PRIMARY KEY,
                        event_type VARCHAR(48) NOT NULL,
                        symbol VARCHAR(32),
                        ts_ms BIGINT NOT NULL,
                        severity SMALLINT NOT NULL DEFAULT 1,
                        direction REAL,
                        source VARCHAR(40) NOT NULL,
                        title TEXT,
                        payload JSONB,
                        dedupe_hash VARCHAR(64) NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_market_events_type_ts ON market_events (event_type, ts_ms DESC)"
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_market_events_symbol_ts ON market_events (symbol, ts_ms DESC)"
                ))
                db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_market_events_ts ON market_events (ts_ms DESC)"
                ))
                db.commit()
                _schema_ready = True
            except Exception as exc:
                db.rollback()
                logger.warning("[market_events] 建表失败: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[market_events] 建表跳过（DB 不可用）: %s", exc)


def publish(events: Iterable[MarketEvent]) -> int:
    """批量写入（ON CONFLICT (dedupe_hash) DO NOTHING）。返回新插入行数。"""
    batch = [e.finalize() for e in events if isinstance(e, MarketEvent) and e.event_type and e.ts_ms]
    if not batch:
        return 0
    ensure_schema()
    inserted = 0
    try:
        from sqlalchemy import text
        db = _db()
        try:
            stmt = text(
                "INSERT INTO market_events (event_type, symbol, ts_ms, severity, direction, source, title, payload, dedupe_hash) "
                "VALUES (:event_type, :symbol, :ts_ms, :severity, :direction, :source, :title, CAST(:payload AS JSONB), :dedupe_hash) "
                "ON CONFLICT (dedupe_hash) DO NOTHING"
            )
            for e in batch:
                res = db.execute(stmt, {
                    "event_type": e.event_type[:48],
                    "symbol": e.symbol,
                    "ts_ms": int(e.ts_ms),
                    "severity": int(e.severity),
                    "direction": e.direction,
                    "source": e.source[:40],
                    "title": e.title,
                    "payload": json.dumps(e.payload or {}, ensure_ascii=False, default=str),
                    "dedupe_hash": e.dedupe_hash[:64],
                })
                inserted += int(res.rowcount or 0)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("[market_events] 写入失败（%d 条）: %s", len(batch), exc)
            return 0
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[market_events] 写入跳过: %s", exc)
        return 0
    if inserted:
        logger.info("[market_events] 新事件 %d 条：%s", inserted,
                    ", ".join(sorted({f"{e.event_type}" for e in batch}))[:200])
    return inserted


def publish_one(**kwargs: Any) -> int:
    return publish([MarketEvent(**kwargs)])


def query(
    *,
    event_types: Optional[Sequence[str]] = None,
    symbol: Optional[str] = None,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    min_severity: Optional[int] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """按类型/币/时间窗查询，按 ts_ms 倒序。symbol 查询同时包含全市场事件（symbol IS NULL）。"""
    ensure_schema()
    conds = ["1=1"]
    params: Dict[str, Any] = {"limit": int(max(1, min(5000, limit)))}
    if event_types:
        types = [str(t) for t in event_types if t]
        if types:
            conds.append("event_type = ANY(:types)")
            params["types"] = types
    if symbol:
        conds.append("(symbol = :symbol OR symbol IS NULL)")
        params["symbol"] = str(symbol).upper()
    if since_ms is not None:
        conds.append("ts_ms >= :since")
        params["since"] = int(since_ms)
    if until_ms is not None:
        conds.append("ts_ms <= :until")
        params["until"] = int(until_ms)
    if min_severity is not None:
        conds.append("severity >= :sev")
        params["sev"] = int(min_severity)
    sql = (
        "SELECT id, event_type, symbol, ts_ms, severity, direction, source, title, payload, dedupe_hash, created_at "
        f"FROM market_events WHERE {' AND '.join(conds)} ORDER BY ts_ms DESC LIMIT :limit"
    )
    try:
        from sqlalchemy import text
        db = _db()
        try:
            rows = db.execute(text(sql), params).fetchall()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[market_events] 查询失败: %s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = r[8]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                pass
        out.append({
            "id": r[0], "event_type": r[1], "symbol": r[2], "ts_ms": int(r[3]),
            "severity": int(r[4]), "direction": (float(r[5]) if r[5] is not None else None),
            "source": r[6], "title": r[7], "payload": payload, "dedupe_hash": r[9],
            "created_at": (r[10].isoformat() if r[10] is not None else None),
        })
    return out


def recent(hours: float = 24.0, **kwargs: Any) -> List[Dict[str, Any]]:
    since = int((time.time() - float(hours) * 3600.0) * 1000)
    return query(since_ms=since, **kwargs)


def get_by_id(event_id: int) -> Optional[Dict[str, Any]]:
    """按主键取单条事件；不存在或失败返回 None。"""
    ensure_schema()
    try:
        from sqlalchemy import text
        db = _db()
        try:
            r = db.execute(
                text(
                    "SELECT id, event_type, symbol, ts_ms, severity, direction, source, title, payload, "
                    "dedupe_hash, created_at FROM market_events WHERE id = :id"
                ),
                {"id": int(event_id)},
            ).fetchone()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[market_events] get_by_id 失败: %s", exc)
        return None
    if not r:
        return None
    payload = r[8]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            pass
    return {
        "id": r[0], "event_type": r[1], "symbol": r[2], "ts_ms": int(r[3]),
        "severity": int(r[4]), "direction": (float(r[5]) if r[5] is not None else None),
        "source": r[6], "title": r[7], "payload": payload, "dedupe_hash": r[9],
        "created_at": (r[10].isoformat() if r[10] is not None else None),
    }


MARKET_WIDE_KEY = "*"


def for_symbols(symbols: Sequence[str], *, hours: float = 24.0, min_severity: int = 2,
                limit: int = 500) -> Dict[str, List[Dict[str, Any]]]:
    """一次查询取一批币近 hours 小时、严重度 ≥ min_severity 的事件，按币分组；
    全市场事件（symbol IS NULL）放在 key `"*"`。供 UnifiedDataPool 快照使用。失败返回 {}。"""
    syms = sorted({str(s).strip().upper() for s in (symbols or []) if s})
    if not syms:
        return {}
    ensure_schema()
    since = int((time.time() - float(hours) * 3600.0) * 1000)
    sql = (
        "SELECT event_type, symbol, ts_ms, severity, direction, source, title, payload "
        "FROM market_events WHERE ts_ms >= :since AND severity >= :sev "
        "AND (symbol = ANY(:syms) OR symbol IS NULL) ORDER BY ts_ms DESC LIMIT :limit"
    )
    try:
        from sqlalchemy import text
        db = _db()
        try:
            rows = db.execute(text(sql), {"since": since, "sev": int(min_severity), "syms": syms,
                                          "limit": int(max(1, min(5000, limit)))}).fetchall()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[market_events] for_symbols 查询失败: %s", exc)
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in syms}
    out[MARKET_WIDE_KEY] = []
    for r in rows:
        payload = r[7]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                pass
        ev = {"event_type": r[0], "symbol": r[1], "ts_ms": int(r[2]), "severity": int(r[3]),
              "direction": (float(r[4]) if r[4] is not None else None), "source": r[5], "title": r[6],
              "payload": payload if isinstance(payload, dict) else {}}
        out.setdefault(r[1] or MARKET_WIDE_KEY, []).append(ev)
    return out


def counts(hours: float = 24.0) -> Dict[str, int]:
    """近 N 小时每类事件数量（看板/新鲜度用）。"""
    ensure_schema()
    since = int((time.time() - float(hours) * 3600.0) * 1000)
    try:
        from sqlalchemy import text
        db = _db()
        try:
            rows = db.execute(text(
                "SELECT event_type, COUNT(*) FROM market_events WHERE ts_ms >= :s GROUP BY event_type ORDER BY 1"
            ), {"s": since}).fetchall()
        finally:
            db.close()
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception as exc:
        logger.warning("[market_events] counts 失败: %s", exc)
        return {}


def latest_by_source() -> Dict[str, Dict[str, Any]]:
    """每个 source 最新一条事件的时间（数据新鲜度）。"""
    ensure_schema()
    try:
        from sqlalchemy import text
        db = _db()
        try:
            rows = db.execute(text(
                "SELECT source, MAX(ts_ms), MAX(created_at), COUNT(*) FROM market_events GROUP BY source ORDER BY 1"
            )).fetchall()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[market_events] latest_by_source 失败: %s", exc)
        return {}
    now_ms = int(time.time() * 1000)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        ts = int(r[1]) if r[1] is not None else None
        out[str(r[0])] = {
            "latest_ts_ms": ts,
            "age_sec": (round((now_ms - ts) / 1000.0, 1) if ts else None),
            "latest_created_at": (r[2].isoformat() if r[2] is not None else None),
            "total": int(r[3]),
        }
    return out


def active_risk_windows(symbol: Optional[str] = None, *, lookback_hours: float = 24.0,
                        min_severity: int = 3) -> List[Dict[str, Any]]:
    """RiskEngine 事件避险窗口读取器：近 N 小时 severity ≥ min_severity 的事件。

    返回每条事件 + 建议窗口（payload.window_hours，缺省按类型给默认值）。
    仅是"可消费的读取器"，是否阻断由 RiskEngine 决定。
    """
    default_window = {
        ANNOUNCEMENT_DELISTING: 72.0,
        ANNOUNCEMENT_MONITORING_TAG: 48.0,
        LIQUIDATION_CASCADE: 2.0,
        LIQUIDATION_MARKET_CASCADE: 4.0,
        FUNDING_EXTREME: 8.0,
        NEWS_HIGH_IMPACT: 6.0,
        MACRO_SCHEDULED: 1.0,
    }
    rows = recent(hours=lookback_hours, symbol=symbol, min_severity=min_severity, limit=500)
    now_ms = int(time.time() * 1000)
    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        window_h = payload.get("window_hours")
        try:
            window_h = float(window_h) if window_h is not None else default_window.get(r["event_type"], 0.0)
        except (TypeError, ValueError):
            window_h = default_window.get(r["event_type"], 0.0)
        if window_h <= 0:
            continue  # 该类型没有避险窗口语义（如巨鲸/OI 突变），只是信息事件
        ends_ms = int(r["ts_ms"] + window_h * 3600_000)
        if ends_ms >= now_ms:
            r2 = dict(r)
            r2["window_hours"] = window_h
            r2["window_ends_ms"] = ends_ms
            out.append(r2)
    return out
