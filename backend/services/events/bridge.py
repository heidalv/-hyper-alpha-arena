# -*- coding: utf-8 -*-
"""既有事件表 → market_events 桥接（5 分钟）。

不重写现有采集器（news_intelligence / aggregate_whale_collector / macro_calendar），
只把满足门槛的行复制进事件总线，dedupe_hash 用源表主键，天然幂等：
  news_events        impact_strength ≥ NEWS_HIGH_IMPACT_MIN（默认 4，量纲 1–5）→ news.high_impact
                     （每个 affected_symbol 一条；无币 → 全市场一条）
  whale_activities   amount_usd ≥ WHALE_LARGE_USD（默认 5000 万）→ whale.large
  macro_events       importance ≥ 4 且 48h 内到期 → macro.scheduled；已出 actual → macro.released

窗口：每轮回看 BRIDGE_LOOKBACK_HOURS（默认 6h，按 created_at），重复行被 dedupe 吞掉。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.services.events import market_events_store as mes

logger = logging.getLogger(__name__)
_LAST: Dict[str, Any] = {}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _db():
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def _to_ms(dt: Any) -> Optional[int]:
    """源表 TIMESTAMP 列都是 naive 本地时间（whale: time.strftime / datetime.now()；news: 带 tz 的
    published_at 经 psycopg 按会话时区落成本地 naive）→ naive 按**系统本地时区**解释。"""
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        return int(dt if dt > 1e11 else dt * 1000)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.astimezone()  # naive → 本地时区
        return int(dt.timestamp() * 1000)
    return None


def _symbols(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        import json
        try:
            v = json.loads(v)
        except Exception:
            v = [s for s in v.replace(";", ",").split(",")]
    if isinstance(v, dict):
        v = list(v.keys())
    out: List[str] = []
    for s in v or []:
        s2 = str(s).strip().upper().replace("USDT", "").replace("/", "")
        if s2 and len(s2) <= 20 and s2 not in out:
            out.append(s2)
    return out[:8]


# ─────────────────────────────────────────────────────────────────────────────
# 纯映射函数（便于单测）
# ─────────────────────────────────────────────────────────────────────────────
def news_to_events(row: Dict[str, Any], *, min_strength: Optional[int] = None) -> List[mes.MarketEvent]:
    # [2026-09-03 修复] 默认阈值原为 7，而 impact_strength 的量纲是 **1–5**
    # （NewsImpact.strength 定义与 LLM prompt 都是 1~5）→ 条件恒不成立，
    # news.high_impact 自上线起一条没产出。改为 4（1–5 量纲下的「强」）。
    thr = int(min_strength if min_strength is not None else _env_float("NEWS_HIGH_IMPACT_MIN", 4))
    strength = row.get("impact_strength")
    try:
        strength = int(strength) if strength is not None else 0
    except (TypeError, ValueError):
        strength = 0
    if strength < thr:
        return []
    ts = _to_ms(row.get("published_at")) or _to_ms(row.get("created_at")) or int(time.time() * 1000)
    direction = row.get("impact_direction")
    try:
        direction = float(direction) if direction is not None else None
    except (TypeError, ValueError):
        direction = None
    sev = 3 if strength < 5 else 4
    syms = _symbols(row.get("affected_symbols")) or [None]
    events: List[mes.MarketEvent] = []
    for s in syms:
        events.append(mes.MarketEvent(
            event_type=mes.NEWS_HIGH_IMPACT, ts_ms=ts, source="news_events", symbol=s, severity=sev,
            direction=direction, title=str(row.get("title") or "")[:300],
            payload={"news_id": row.get("id"), "impact_strength": strength, "impact_duration": row.get("impact_duration"),
                     "event_category": row.get("event_category"), "confidence": row.get("confidence"),
                     "source_name": row.get("source"), "url": row.get("url"),
                     "window_hours": 6.0 if (direction or 0) < 0 else 3.0},
            dedupe_hash=mes.make_dedupe_hash("news", row.get("id"), s or ""),
        ))
    return events


def whale_to_event(row: Dict[str, Any], *, min_usd: Optional[float] = None) -> Optional[mes.MarketEvent]:
    thr = min_usd if min_usd is not None else _env_float("WHALE_LARGE_USD", 50_000_000.0)
    amt = row.get("amount_usd")
    try:
        amt = float(amt) if amt is not None else 0.0
    except (TypeError, ValueError):
        amt = 0.0
    if amt < thr:
        return None
    ts = _to_ms(row.get("timestamp")) or _to_ms(row.get("created_at")) or int(time.time() * 1000)
    direction = row.get("signal_direction")
    try:
        direction = float(direction) if direction is not None else None
    except (TypeError, ValueError):
        direction = None
    if direction is None:
        d = str(row.get("direction") or "").lower()
        direction = 0.5 if d in ("buy", "long", "inflow_to_wallet", "withdraw") else (-0.5 if d in ("sell", "short", "inflow_to_exchange", "deposit") else None)
    sym = (str(row.get("symbol") or "").upper().replace("USDT", "") or None)
    sev = 2 if amt < thr * 4 else 3
    # 同一笔转账常被链上追踪器在数分钟内重复记录（金额完全相同）→ 按 (币, 类型, 整数金额, 10 分钟桶) 去重
    return mes.MarketEvent(
        event_type=mes.WHALE_LARGE, ts_ms=ts, source="whale_activities", symbol=sym, severity=sev, direction=direction,
        title=f"{sym or '?'} 巨鲸 {row.get('activity_type') or ''} ${amt:,.0f}".strip(),
        payload={"whale_id": row.get("id"), "activity_type": row.get("activity_type"), "amount_usd": amt,
                 "from_entity": row.get("from_entity"), "to_entity": row.get("to_entity"),
                 "blockchain": row.get("blockchain"), "direction": row.get("direction")},
        dedupe_hash=mes.make_dedupe_hash("whale", sym or "", row.get("activity_type") or "", int(amt), ts // 600_000),
    )


def macro_to_event(row: Dict[str, Any], *, now_ms: Optional[int] = None) -> Optional[mes.MarketEvent]:
    now = now_ms or int(time.time() * 1000)
    try:
        importance = int(row.get("importance") or 0)
    except (TypeError, ValueError):
        importance = 0
    if importance < 4:
        return None
    ts = _to_ms(row.get("scheduled_at"))
    if not ts:
        return None
    released = row.get("actual") is not None
    if released:
        if now - ts > 24 * 3600_000:
            return None
        et = mes.MACRO_RELEASED
        direction = row.get("impact_direction")
        try:
            direction = float(direction) if direction is not None else None
        except (TypeError, ValueError):
            direction = None
        sev = 3 if importance >= 5 else 2
        title = f"{row.get('event')} 公布：{row.get('actual')} (预期 {row.get('forecast')}, 前值 {row.get('previous')})"
    else:
        if ts - now > 48 * 3600_000 or ts < now - 3600_000:
            return None
        et = mes.MACRO_SCHEDULED
        direction = None
        sev = 3 if importance >= 5 else 2
        title = f"{row.get('event')} 将于 {datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%m-%d %H:%M')} UTC 公布"
    return mes.MarketEvent(
        event_type=et, ts_ms=ts, source="macro_events", symbol=None, severity=sev, direction=direction, title=title,
        payload={"macro_id": row.get("id"), "event": row.get("event"), "country": row.get("country"),
                 "importance": importance, "forecast": row.get("forecast"), "actual": row.get("actual"),
                 "previous": row.get("previous"), "window_hours": 1.0},
        dedupe_hash=mes.make_dedupe_hash("macro", row.get("id"), et),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 任务
# ─────────────────────────────────────────────────────────────────────────────
def _rows(sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    db = _db()
    try:
        res = db.execute(text(sql), params)
        cols = list(res.keys())
        return [dict(zip(cols, r)) for r in res.fetchall()]
    finally:
        db.close()


def bridge_once(lookback_hours: Optional[float] = None) -> Dict[str, Any]:
    from backend.core.tenant import set_system_identity
    set_system_identity()
    t0 = time.time()
    lb = float(lookback_hours if lookback_hours is not None else _env_float("BRIDGE_LOOKBACK_HOURS", 6.0))
    # 源表 created_at/scheduled_at 为本地 naive → 用本地 naive 比较
    since_naive = datetime.now() - timedelta(hours=lb)
    events: List[mes.MarketEvent] = []
    counts: Dict[str, int] = {"news": 0, "whale": 0, "macro": 0}
    errors: Dict[str, str] = {}
    try:
        # [2026-09-08 修复] 默认值 7 超出 impact_strength 的 1–5 量纲（env 缺失时
        # 桥接恒不产出），与 news_to_events 的默认 4 对齐。
        thr = int(_env_float("NEWS_HIGH_IMPACT_MIN", 4))
        for r in _rows(
            "SELECT id, source, title, url, published_at, impact_direction, impact_strength, impact_duration, "
            "affected_symbols, event_category, confidence, created_at FROM news_events "
            "WHERE created_at >= :since AND impact_strength >= :thr ORDER BY id DESC LIMIT 500",
            {"since": since_naive, "thr": thr},
        ):
            evs = news_to_events(r, min_strength=thr)
            counts["news"] += len(evs)
            events.extend(evs)
    except Exception as exc:
        errors["news"] = str(exc)[:200]
    try:
        thr_w = _env_float("WHALE_LARGE_USD", 50_000_000.0)
        for r in _rows(
            "SELECT id, activity_type, symbol, direction, amount_usd, from_entity, to_entity, blockchain, "
            "signal_direction, timestamp, created_at FROM whale_activities "
            "WHERE created_at >= :since AND amount_usd >= :thr ORDER BY id DESC LIMIT 500",
            {"since": since_naive, "thr": thr_w},
        ):
            ev = whale_to_event(r, min_usd=thr_w)
            if ev:
                counts["whale"] += 1
                events.append(ev)
    except Exception as exc:
        errors["whale"] = str(exc)[:200]
    try:
        now_naive = datetime.now()
        for r in _rows(
            "SELECT id, event, country, importance, scheduled_at, forecast, actual, previous, impact_direction "
            "FROM macro_events WHERE importance >= 4 AND scheduled_at BETWEEN :a AND :b ORDER BY scheduled_at",
            {"a": now_naive - timedelta(hours=24), "b": now_naive + timedelta(hours=48)},
        ):
            ev = macro_to_event(r)
            if ev:
                counts["macro"] += 1
                events.append(ev)
    except Exception as exc:
        errors["macro"] = str(exc)[:200]
    written = mes.publish(events) if events else 0
    summary = {"candidates": counts, "events_written": written, "errors": errors,
               "lookback_hours": lb, "elapsed_ms": int((time.time() - t0) * 1000), "as_of": int(time.time() * 1000)}
    _LAST.clear()
    _LAST.update(summary)
    logger.info("[events.bridge] 候选 %s → 新事件 %d%s", counts, written, f" 错误 {errors}" if errors else "")
    return summary


def last_summary() -> Dict[str, Any]:
    return dict(_LAST)
