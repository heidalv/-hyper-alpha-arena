# -*- coding: utf-8 -*-
"""全币池资金费（v2 E2c / v3 p0-event-data）。

两件事：
1) 结算费率一年回填 `backfill_settled_funding()`：
   Binance `/fapi/v1/fundingRate?symbol=&startTime=&limit=1000`（公开、无 Key）对**全部 USDT 永续**
   （exchangeInfo TRADING，≈520 个）分页拉取历史结算费率 → perp_funding(exchange='binance',
   symbol=基础币, timestamp=fundingTime, funding_rate, mark_price)，ON CONFLICT DO NOTHING。
   进度落 backend/data/events/funding_backfill_state.json（每币 last_ts），带时间预算可断点续跑；
   每日任务 funding_backfill 只做增量。
   语义说明：perp_funding 里既有 5 分钟 premiumIndex 快照（预测费率，现有采集器），也有结算
   费率（本模块 + 旧 ccxt 回填）。研究用途以"结算时刻附近的行"为准（timestamp 落在 00/08/16 UTC）。

2) 极端费率扫描 `scan_extremes()`（10 分钟）：读 perp_funding 近 20 分钟每 (exchange, symbol) 最新一行，
   折算到 8h 口径（hyperliquid 为 1h 费率 ×8），|rate_8h| ≥ FUNDING_EXTREME_8H（默认 0.10%）→
   market_events funding.extreme（每 8h 桶一条）。这也是方案第六节 `funding_universe_scan` Skill 的算法核。

全量入库（premiumIndex 快照覆盖全部币）由 multi_venue_funding_collector 的 `*` 通配开启
（MULTI_VENUE_FUNDING_SYMBOLS=*），不在此重复实现。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.services.events import market_events_store as mes
from backend.services.events._http import get_json

logger = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
_ROOT = Path(__file__).resolve().parents[2]  # backend/
STATE_DIR = _ROOT / "data" / "events"
STATE_FILE = STATE_DIR / "funding_backfill_state.json"

_UNIVERSE_CACHE: Dict[str, Any] = {"ts": 0.0, "rows": []}
_UNIVERSE_LOCK = threading.Lock()
_BACKFILL_LOCK = threading.Lock()
_LAST: Dict[str, Any] = {}

# 各场所原始费率结算周期（小时），用于折算到 8h 口径
FUNDING_INTERVAL_HOURS: Dict[str, float] = {
    "hyperliquid": 1.0,
    "binance": 8.0, "bybit": 8.0, "okx": 8.0, "gateio": 8.0, "asterdex": 8.0,
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 币池
# ─────────────────────────────────────────────────────────────────────────────
def list_usdt_perps(force: bool = False) -> List[Dict[str, Any]]:
    """[{pair, base, onboard_ms}]（exchangeInfo, TRADING, PERPETUAL, quote=USDT），缓存 1h。"""
    with _UNIVERSE_LOCK:
        if not force and _UNIVERSE_CACHE["rows"] and time.time() - _UNIVERSE_CACHE["ts"] < 3600:
            return list(_UNIVERSE_CACHE["rows"])
    data = get_json(f"{FAPI}/fapi/v1/exchangeInfo", timeout=25)
    rows: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        for s in data.get("symbols", []) or []:
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
                continue
            pair = str(s.get("symbol") or "")
            base = str(s.get("baseAsset") or pair[:-4])
            try:
                onboard = int(s.get("onboardDate") or 0)
            except (TypeError, ValueError):
                onboard = 0
            rows.append({"pair": pair, "base": base.upper(), "onboard_ms": onboard})
    if rows:
        with _UNIVERSE_LOCK:
            _UNIVERSE_CACHE["ts"] = time.time()
            _UNIVERSE_CACHE["rows"] = list(rows)
        return rows
    with _UNIVERSE_LOCK:
        return list(_UNIVERSE_CACHE["rows"])


# ─────────────────────────────────────────────────────────────────────────────
# 回填
# ─────────────────────────────────────────────────────────────────────────────
def _load_state() -> Dict[str, Any]:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("[funding_universe] 读状态失败: %s", exc)
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=0), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        logger.debug("[funding_universe] 写状态失败: %s", exc)


def parse_funding_rows(payload: Any) -> List[Tuple[int, float, Optional[float]]]:
    """纯函数：/fapi/v1/fundingRate 响应 → [(fundingTime, rate, markPrice)] 升序去重。"""
    out: Dict[int, Tuple[float, Optional[float]]] = {}
    if not isinstance(payload, list):
        return []
    for r in payload:
        try:
            ts = int(r.get("fundingTime"))
            rate = float(r.get("fundingRate"))
        except (TypeError, ValueError, AttributeError):
            continue
        out[ts] = (rate, _f(r.get("markPrice")))
    return [(ts, v[0], v[1]) for ts, v in sorted(out.items())]


def _db():
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def _insert_rates(base: str, rows: List[Tuple[int, float, Optional[float]]]) -> int:
    if not rows:
        return 0
    try:
        from sqlalchemy import text
        db = _db()
        try:
            res = db.execute(text(
                "INSERT INTO perp_funding (exchange, symbol, timestamp, funding_rate, mark_price) "
                "VALUES ('binance', :symbol, :ts, :rate, :mark) ON CONFLICT (exchange, symbol, timestamp) DO NOTHING"
            ), [{"symbol": base[:20], "ts": int(ts), "rate": rate, "mark": mark} for ts, rate, mark in rows])
            db.commit()
            n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else len(rows)
            return int(n)
        except Exception as exc:
            db.rollback()
            logger.warning("[funding_universe] 写 %s %d 行失败: %s", base, len(rows), exc)
            return 0
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[funding_universe] 写入跳过: %s", exc)
        return 0


def _backfill_sleep_default() -> float:
    """`/fapi/v1/fundingRate` 与 fundingInfo 共享 500 次/5 分钟/IP 的独立限额 → 每请求 ≥ 0.6s。"""
    try:
        return max(0.6, float(os.getenv("FUNDING_BACKFILL_SLEEP_SEC", "") or 0.65))
    except (TypeError, ValueError):
        return 0.65


def backfill_symbol(pair: str, base: str, since_ms: int, *, until_ms: Optional[int] = None,
                    page_limit: int = 1000, max_pages: int = 40, sleep_sec: Optional[float] = None) -> Tuple[int, int, int, bool]:
    """单币分页回填。返回 (fetched, inserted, last_ts, ok)。ok=False 表示有请求失败（429/网络），
    调用方不得把该币标记为"已刷新"，否则限速失败会被当成"无数据"而 8 小时内不再重试。"""
    if sleep_sec is None:
        sleep_sec = _backfill_sleep_default()
    fetched = inserted = 0
    ok = True
    cursor = int(since_ms)
    last_ts = cursor
    until = int(until_ms or time.time() * 1000)
    for _ in range(max_pages):
        if cursor >= until:
            break
        data = get_json(f"{FAPI}/fapi/v1/fundingRate", {"symbol": pair, "startTime": cursor, "limit": page_limit},
                        timeout=25, retries=2)
        if data is None:
            ok = False
            break
        rows = parse_funding_rows(data)
        if not rows:
            break
        fetched += len(rows)
        inserted += _insert_rates(base, rows)
        last_ts = rows[-1][0]
        if len(rows) < page_limit:
            break
        cursor = last_ts + 1
        if sleep_sec:
            time.sleep(sleep_sec)
    return fetched, inserted, last_ts, ok


def backfill_settled_funding(days: int = 365, *, max_seconds: float = 1500.0, sleep_sec: Optional[float] = None,
                             symbols: Optional[List[str]] = None, refresh_hours: float = 8.0) -> Dict[str, Any]:
    """全币池结算费率回填（可断点续跑）。

    - 首次：每币从 max(now-days, onboard) 开始；之后：从 state.last_ts+1 增量；
    - 最近 refresh_hours 内已刷过的币本轮跳过（避免每日任务重复扫全池）；
    - 超过 max_seconds 停止并保存进度，下一轮从未完成的币继续；
    - 节拍：币间与页间都按 sleep_sec（默认 0.65s）间隔，遵守 fundingRate 500 次/5 分钟的独立限额。
    """
    if not _BACKFILL_LOCK.acquire(blocking=False):
        return {"skipped": True, "reason": "another backfill running"}
    if sleep_sec is None:
        sleep_sec = _backfill_sleep_default()
    t0 = time.time()
    try:
        from backend.core.tenant import set_system_identity
        set_system_identity()
        universe = list_usdt_perps()
        if symbols:
            want = {s.upper() for s in symbols}
            universe = [u for u in universe if u["base"] in want or u["pair"] in want]
        state = _load_state()
        per = state.setdefault("symbols", {})
        now_ms = int(time.time() * 1000)
        floor_ms = now_ms - int(days) * 86400_000
        done = 0
        failed = 0
        fetched_total = inserted_total = 0
        skipped_fresh = 0
        remaining: List[str] = []
        # 未完成/最久未刷的排前面
        ordered = sorted(universe, key=lambda u: int((per.get(u["pair"]) or {}).get("checked_ms") or 0))
        for u in ordered:
            if time.time() - t0 > max_seconds:
                remaining.append(u["pair"])
                continue
            rec = per.get(u["pair"]) or {}
            checked = int(rec.get("checked_ms") or 0)
            if checked and now_ms - checked < refresh_hours * 3600_000:
                skipped_fresh += 1
                continue
            since = max(int(rec.get("last_ts") or 0) + 1, floor_ms, int(u.get("onboard_ms") or 0))
            fetched, inserted, last_ts, ok = backfill_symbol(u["pair"], u["base"], since, sleep_sec=sleep_sec)
            fetched_total += fetched
            inserted_total += inserted
            new_rec = {"base": u["base"], "last_ts": max(int(rec.get("last_ts") or 0), last_ts),
                       "rows": int(rec.get("rows") or 0) + inserted,
                       "checked_ms": int(time.time() * 1000) if ok else checked}
            if not ok:
                failed += 1
                new_rec["last_fail_ms"] = int(time.time() * 1000)
            per[u["pair"]] = new_rec
            done += 1
            if done % 25 == 0:
                _save_state(state)
            if sleep_sec:
                time.sleep(sleep_sec)
        state["last_run_ms"] = int(time.time() * 1000)
        state["universe_size"] = len(universe)
        _save_state(state)
        summary = {
            "universe": len(universe), "symbols_processed": done, "symbols_failed": failed,
            "symbols_skipped_fresh": skipped_fresh, "symbols_remaining": len(remaining),
            "fetched": fetched_total, "inserted": inserted_total,
            "elapsed_ms": int((time.time() - t0) * 1000), "days": days, "as_of": int(time.time() * 1000),
        }
        _LAST["backfill"] = summary
        logger.info("[funding_universe] 回填：处理 %d/%d 币（失败 %d，跳过 %d 新鲜，余 %d），拉取 %d 行，新增 %d 行，%dms",
                    done, len(universe), failed, skipped_fresh, len(remaining), fetched_total, inserted_total,
                    summary["elapsed_ms"])
        return summary
    finally:
        _BACKFILL_LOCK.release()


def start_backfill_thread(days: int = 365, max_seconds: float = 1500.0) -> bool:
    """后台线程跑一轮回填（用于进程启动后立即补历史，不阻塞调度器）。"""
    if _BACKFILL_LOCK.locked():
        return False

    def _run() -> None:
        try:
            backfill_settled_funding(days=days, max_seconds=max_seconds)
        except Exception as exc:
            logger.warning("[funding_universe] 后台回填异常: %s", exc)

    threading.Thread(target=_run, name="funding-universe-backfill", daemon=True).start()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 极端费率扫描
# ─────────────────────────────────────────────────────────────────────────────
def rate_8h(exchange: str, rate: float) -> float:
    hrs = FUNDING_INTERVAL_HOURS.get((exchange or "").lower(), 8.0)
    return float(rate) * (8.0 / hrs) if hrs > 0 else float(rate)


def classify_extreme(exchange: str, symbol: str, rate: float, ts_ms: int, *,
                     threshold_8h: Optional[float] = None) -> Optional[mes.MarketEvent]:
    """纯函数：单行费率 → funding.extreme 事件或 None。"""
    thr = threshold_8h if threshold_8h is not None else _env_float("FUNDING_EXTREME_8H", 0.001)
    r8 = rate_8h(exchange, rate)
    if abs(r8) < thr:
        return None
    a = abs(r8)
    sev = 2 if a < thr * 2 else (3 if a < thr * 5 else 4)
    bucket = int(ts_ms) // (8 * 3600_000)
    apr = r8 * 3 * 365 * 100.0
    return mes.MarketEvent(
        event_type=mes.FUNDING_EXTREME, ts_ms=int(ts_ms), source="funding_collector", symbol=symbol,
        severity=sev, direction=(-0.5 if r8 > 0 else 0.5),
        title=f"{exchange} {symbol} 资金费 {r8 * 100:+.3f}%/8h（≈{apr:+.0f}% APR）",
        payload={"exchange": exchange, "rate_raw": float(rate), "rate_8h": r8, "apr_pct": round(apr, 1),
                 "threshold_8h": thr, "window_hours": 8.0},
        dedupe_hash=mes.make_dedupe_hash("funding_extreme", exchange, symbol, bucket),
    )


def scan_extremes(window_min: int = 20, threshold_8h: Optional[float] = None) -> Dict[str, Any]:
    """读近 window_min 分钟每 (exchange, symbol) 最新费率 → 极端事件。"""
    from backend.core.tenant import set_system_identity
    set_system_identity()
    t0 = time.time()
    since = int((time.time() - window_min * 60) * 1000)
    rows: List[Tuple[str, str, float, int]] = []
    try:
        from sqlalchemy import text
        db = _db()
        try:
            res = db.execute(text(
                "SELECT DISTINCT ON (exchange, symbol) exchange, symbol, funding_rate, timestamp FROM perp_funding "
                "WHERE timestamp >= :since ORDER BY exchange, symbol, timestamp DESC"
            ), {"since": since}).fetchall()
        finally:
            db.close()
        rows = [(str(r[0]), str(r[1]), float(r[2]), int(r[3])) for r in res if r[2] is not None]
    except Exception as exc:
        logger.warning("[funding_universe] 扫描读库失败: %s", exc)
        return {"error": str(exc)[:200], "scanned": 0}
    events: List[mes.MarketEvent] = []
    for ex, sym, rate, ts in rows:
        ev = classify_extreme(ex, sym, rate, ts, threshold_8h=threshold_8h)
        if ev is not None:
            events.append(ev)
    written = mes.publish(events) if events else 0
    top = sorted(rows, key=lambda r: abs(rate_8h(r[0], r[2])), reverse=True)[:10]
    summary = {
        "scanned": len(rows), "venues": sorted({r[0] for r in rows}), "extremes": len(events), "events_written": written,
        "top": [{"exchange": r[0], "symbol": r[1], "rate_8h": round(rate_8h(r[0], r[2]), 6)} for r in top],
        "elapsed_ms": int((time.time() - t0) * 1000), "as_of": int(time.time() * 1000),
    }
    _LAST["scan"] = summary
    logger.info("[funding_universe] 极端费率扫描：%d 行（%s）→ %d 极端，新事件 %d", len(rows),
                ",".join(summary["venues"]), len(events), written)
    return summary


def last_summary() -> Dict[str, Any]:
    return dict(_LAST)


def backfill_state() -> Dict[str, Any]:
    st = _load_state()
    per = st.get("symbols") or {}
    return {
        "universe_size": st.get("universe_size"), "last_run_ms": st.get("last_run_ms"),
        "symbols_tracked": len(per), "rows_inserted_total": sum(int(v.get("rows") or 0) for v in per.values()),
        "oldest_checked_ms": min((int(v.get("checked_ms") or 0) for v in per.values()), default=None),
    }
