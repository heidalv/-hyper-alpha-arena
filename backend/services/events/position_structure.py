# -*- coding: utf-8 -*-
"""Binance 持仓结构采集（免费 /futures/data/*）→ position_structure + market_events。

端点（period=1h）：
  openInterestHist              sumOpenInterest / sumOpenInterestValue
  globalLongShortAccountRatio   longShortRatio / longAccount（散户账户多空比）
  topLongShortPositionRatio     大户持仓多空比
  topLongShortAccountRatio      大户账户多空比
  takerlongshortRatio           主动买卖比 buySellRatio / buyVol / sellVol

币池：24h 成交额 Top-N（POSITION_STRUCTURE_TOP_N=60）∪ 核心币；首次见到的币回填 500 根（≈20 天）。
合并：同一 (symbol, ts) 五个端点各自 upsert，COALESCE 保留已有值 → 一行一个小时。
事件：OI 名义 1 小时变动 ≥ POS_OI_JUMP_PCT（默认 8%）→ position.oi_jump（severity 2/3）。

所有请求走 events._http.get_json（代理 + 重试）；单币任一端点失败只影响该字段。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.services.events import market_events_store as mes
from backend.services.events._http import get_json

logger = logging.getLogger(__name__)

FUTURES_DATA = "https://fapi.binance.com/futures/data"
TICKER_24H = "https://fapi.binance.com/fapi/v1/ticker/24hr"
CORE_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "SUI", "LTC", "BCH"]
_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD")

_schema_ready = False
_schema_lock = threading.Lock()
_LAST: Dict[str, Any] = {}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


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
# 纯函数：五端点响应 → {ts_ms: row}
# ─────────────────────────────────────────────────────────────────────────────
def merge_rows(symbol: str, *, oi: Iterable[Dict] = (), global_ls: Iterable[Dict] = (),
               top_pos: Iterable[Dict] = (), top_acct: Iterable[Dict] = (),
               taker: Iterable[Dict] = ()) -> Dict[int, Dict[str, Any]]:
    rows: Dict[int, Dict[str, Any]] = {}

    def _row(ts: Any) -> Optional[Dict[str, Any]]:
        try:
            t = int(ts)
        except (TypeError, ValueError):
            return None
        return rows.setdefault(t, {"symbol": symbol, "ts_ms": t})

    for r in oi or ():
        row = _row(r.get("timestamp"))
        if row is not None:
            row["open_interest"] = _f(r.get("sumOpenInterest"))
            row["open_interest_value"] = _f(r.get("sumOpenInterestValue"))
    for r in global_ls or ():
        row = _row(r.get("timestamp"))
        if row is not None:
            row["global_ls_ratio"] = _f(r.get("longShortRatio"))
            row["global_long_pct"] = _f(r.get("longAccount"))
    for r in top_pos or ():
        row = _row(r.get("timestamp"))
        if row is not None:
            row["top_position_ls_ratio"] = _f(r.get("longShortRatio"))
    for r in top_acct or ():
        row = _row(r.get("timestamp"))
        if row is not None:
            row["top_account_ls_ratio"] = _f(r.get("longShortRatio"))
    for r in taker or ():
        row = _row(r.get("timestamp"))
        if row is not None:
            row["taker_buy_sell_ratio"] = _f(r.get("buySellRatio"))
            row["taker_buy_vol"] = _f(r.get("buyVol"))
            row["taker_sell_vol"] = _f(r.get("sellVol"))
    return rows


def detect_oi_jumps(symbol: str, rows: Dict[int, Dict[str, Any]], *, prev_value: Optional[float] = None,
                    jump_pct: Optional[float] = None) -> List[mes.MarketEvent]:
    """按时间顺序比较相邻小时 OI 名义变动；≥ jump_pct 发 position.oi_jump。"""
    thr = jump_pct if jump_pct is not None else _env_float("POS_OI_JUMP_PCT", 8.0)
    events: List[mes.MarketEvent] = []
    prev = prev_value
    for ts in sorted(rows):
        cur = rows[ts].get("open_interest_value")
        if cur is None:
            continue
        if prev and prev > 0:
            chg = (cur - prev) / prev * 100.0
            if abs(chg) >= thr:
                sev = 2 if abs(chg) < thr * 2 else 3
                events.append(mes.MarketEvent(
                    event_type=mes.POSITION_OI_JUMP, ts_ms=ts, source="binance_futures_data", symbol=symbol,
                    severity=sev, direction=(0.3 if chg > 0 else -0.3),
                    title=f"{symbol} 1h OI 名义变动 {chg:+.1f}%（${prev:,.0f} → ${cur:,.0f}）",
                    payload={"oi_value_prev": prev, "oi_value": cur, "change_pct": round(chg, 3),
                             "global_ls_ratio": rows[ts].get("global_ls_ratio"),
                             "top_position_ls_ratio": rows[ts].get("top_position_ls_ratio"),
                             "taker_buy_sell_ratio": rows[ts].get("taker_buy_sell_ratio")},
                    dedupe_hash=mes.make_dedupe_hash("oi_jump", symbol, ts),
                ))
        prev = cur
    return events


# ─────────────────────────────────────────────────────────────────────────────
# 币池
# ─────────────────────────────────────────────────────────────────────────────
def _pair_to_base(pair: str) -> Optional[str]:
    p = (pair or "").upper()
    for suf in _QUOTE_SUFFIXES:
        if p.endswith(suf) and len(p) > len(suf):
            return p[: -len(suf)]
    return None


def select_universe(top_n: Optional[int] = None) -> List[str]:
    """24h 成交额 Top-N USDT 永续 ∪ 核心币（基础符号，大写）。接口失败 → 仅核心币。"""
    n = top_n if top_n is not None else _env_int("POSITION_STRUCTURE_TOP_N", 60)
    bases: List[str] = list(CORE_SYMBOLS)
    data = get_json(TICKER_24H, timeout=20)
    if isinstance(data, list):
        ranked: List[Tuple[float, str]] = []
        for t in data:
            pair = str(t.get("symbol") or "")
            if not pair.endswith("USDT"):
                continue
            base = _pair_to_base(pair)
            qv = _f(t.get("quoteVolume")) or 0.0
            if base and qv > 0:
                ranked.append((qv, base))
        ranked.sort(reverse=True)
        for _, b in ranked[: max(0, n)]:
            if b not in bases:
                bases.append(b)
    return bases


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
                    CREATE TABLE IF NOT EXISTS position_structure (
                        id BIGSERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL DEFAULT 'binance',
                        symbol VARCHAR(32) NOT NULL,
                        period VARCHAR(8) NOT NULL DEFAULT '1h',
                        ts_ms BIGINT NOT NULL,
                        open_interest NUMERIC(24, 6),
                        open_interest_value NUMERIC(24, 2),
                        global_ls_ratio NUMERIC(14, 6),
                        global_long_pct NUMERIC(10, 6),
                        top_position_ls_ratio NUMERIC(14, 6),
                        top_account_ls_ratio NUMERIC(14, 6),
                        taker_buy_sell_ratio NUMERIC(14, 6),
                        taker_buy_vol NUMERIC(24, 6),
                        taker_sell_vol NUMERIC(24, 6),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CONSTRAINT uq_position_structure UNIQUE (exchange, symbol, period, ts_ms)
                    )
                    """
                ))
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_pos_struct_symbol_ts ON position_structure (symbol, ts_ms DESC)"))
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_pos_struct_ts ON position_structure (ts_ms DESC)"))
                db.commit()
                _schema_ready = True
            except Exception as exc:
                db.rollback()
                logger.warning("[position_structure] 建表失败: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[position_structure] 建表跳过: %s", exc)


_COLS = ("open_interest", "open_interest_value", "global_ls_ratio", "global_long_pct", "top_position_ls_ratio",
         "top_account_ls_ratio", "taker_buy_sell_ratio", "taker_buy_vol", "taker_sell_vol")


def upsert_rows(rows: Iterable[Dict[str, Any]], period: str = "1h") -> int:
    batch = [r for r in rows if r.get("symbol") and r.get("ts_ms")]
    if not batch:
        return 0
    ensure_schema()
    set_clause = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, position_structure.{c})" for c in _COLS)
    sql = (
        "INSERT INTO position_structure (exchange, symbol, period, ts_ms, " + ", ".join(_COLS) + ") "
        "VALUES ('binance', :symbol, :period, :ts_ms, " + ", ".join(f":{c}" for c in _COLS) + ") "
        f"ON CONFLICT ON CONSTRAINT uq_position_structure DO UPDATE SET {set_clause}, updated_at = now()"
    )
    try:
        from sqlalchemy import text
        db = _db()
        try:
            params = []
            for r in batch:
                p = {"symbol": str(r["symbol"]).upper()[:32], "period": period, "ts_ms": int(r["ts_ms"])}
                for c in _COLS:
                    p[c] = r.get(c)
                params.append(p)
            res = db.execute(text(sql), params)
            db.commit()
            return int(res.rowcount or 0) if res.rowcount is not None and res.rowcount >= 0 else len(params)
        except Exception as exc:
            db.rollback()
            logger.warning("[position_structure] upsert 失败: %s", exc)
            return 0
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[position_structure] upsert 跳过: %s", exc)
        return 0


def _latest_state(symbols: List[str]) -> Dict[str, Tuple[int, Optional[float]]]:
    """每币最新 (ts_ms, open_interest_value)；无记录的币不在返回里（→ 触发首次回填）。"""
    ensure_schema()
    out: Dict[str, Tuple[int, Optional[float]]] = {}
    if not symbols:
        return out
    try:
        from sqlalchemy import text
        db = _db()
        try:
            rows = db.execute(text(
                "SELECT DISTINCT ON (symbol) symbol, ts_ms, open_interest_value FROM position_structure "
                "WHERE exchange = 'binance' AND period = '1h' AND symbol = ANY(:syms) ORDER BY symbol, ts_ms DESC"
            ), {"syms": symbols}).fetchall()
        finally:
            db.close()
        for r in rows:
            out[str(r[0])] = (int(r[1]), (float(r[2]) if r[2] is not None else None))
    except Exception as exc:
        logger.debug("[position_structure] latest_state 失败: %s", exc)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 采集
# ─────────────────────────────────────────────────────────────────────────────
def fetch_symbol(base: str, *, period: str = "1h", limit: int = 3) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, str]]:
    pair = f"{base}USDT"
    diag: Dict[str, str] = {}

    def _get(path: str) -> List[Dict]:
        data = get_json(f"{FUTURES_DATA}/{path}", {"symbol": pair, "period": period, "limit": int(limit)}, timeout=20, retries=1)
        if isinstance(data, list):
            diag[path] = "ok"
            return data
        diag[path] = "error"
        return []

    oi = _get("openInterestHist")
    g = _get("globalLongShortAccountRatio")
    tp = _get("topLongShortPositionRatio")
    ta = _get("topLongShortAccountRatio")
    tk = _get("takerlongshortRatio")
    return merge_rows(base, oi=oi, global_ls=g, top_pos=tp, top_acct=ta, taker=tk), diag


def collect_once(symbols: Optional[List[str]] = None, *, limit: int = 3, backfill_limit: int = 500,
                 sleep_sec: float = 0.12, max_seconds: float = 600.0) -> Dict[str, Any]:
    """一轮采集：币池 → 五端点 → upsert → OI 突变事件。首次见到的币拉 backfill_limit 根。"""
    from backend.core.tenant import set_system_identity
    set_system_identity()
    t0 = time.time()
    universe = symbols or select_universe()
    state = _latest_state(universe)
    total_rows = 0
    events: List[mes.MarketEvent] = []
    ok_symbols = 0
    failed: List[str] = []
    backfilled: List[str] = []
    for base in universe:
        if time.time() - t0 > max_seconds:
            logger.warning("[position_structure] 超时预算 %.0fs，剩余币下一轮继续", max_seconds)
            break
        lim = limit if base in state else backfill_limit
        if base not in state:
            backfilled.append(base)
        rows, diag = fetch_symbol(base, limit=lim)
        if not rows:
            failed.append(base)
            continue
        ok_symbols += 1
        prev_ts, prev_val = state.get(base, (0, None))
        new_rows = {ts: r for ts, r in rows.items() if ts > prev_ts} if prev_ts else rows
        events.extend(detect_oi_jumps(base, new_rows, prev_value=prev_val))
        total_rows += upsert_rows(rows.values())
        if sleep_sec:
            time.sleep(sleep_sec)
    ev_written = mes.publish(events) if events else 0
    summary = {
        "universe": len(universe), "symbols_ok": ok_symbols, "symbols_failed": failed[:20],
        "rows_upserted": total_rows, "backfilled_symbols": backfilled[:30], "events_written": ev_written,
        "elapsed_ms": int((time.time() - t0) * 1000), "as_of": int(time.time() * 1000),
    }
    _LAST.clear()
    _LAST.update(summary)
    logger.info("[position_structure] 完成：币 %d/%d 行 %d 事件 %d 用时 %dms%s", ok_symbols, len(universe),
                total_rows, ev_written, summary["elapsed_ms"], f" 首采 {len(backfilled)}" if backfilled else "")
    return summary


def last_summary() -> Dict[str, Any]:
    return dict(_LAST)


def latest(symbol: str) -> Optional[Dict[str, Any]]:
    """最新一行（供 ContextPack / Agent）。"""
    ensure_schema()
    try:
        from sqlalchemy import text
        db = _db()
        try:
            r = db.execute(text(
                "SELECT ts_ms, " + ", ".join(_COLS) + " FROM position_structure WHERE exchange = 'binance' AND period = '1h' "
                "AND symbol = :s ORDER BY ts_ms DESC LIMIT 1"
            ), {"s": symbol.upper()}).fetchone()
        finally:
            db.close()
    except Exception:
        return None
    if not r:
        return None
    out = {"symbol": symbol.upper(), "ts_ms": int(r[0])}
    for i, c in enumerate(_COLS, start=1):
        out[c] = float(r[i]) if r[i] is not None else None
    return out
