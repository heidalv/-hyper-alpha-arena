# -*- coding: utf-8 -*-
"""[h903 ?? 2026-10-07] ????????? 锟� API ??(?????)?

?????????????? /api/hft ?????????:
  ?? overview / ???? board / ?? account / ?? hero / ?? fills /
  ?? config / ???? universe/live / ?? evolution / ???? equity-series /
  ?? account/reset / ?? control/* / ?? live/* / ping

???(????):
  锟� lane_registry.meta_json       ?? ??(symbols)/??(params)
  锟� lane_runtime_state.state_json ?? ?????(qty/avg_mid/quote_bid/quote_ask)
  锟� lane_ledger                   ?? ????/??(????)
  锟� arbitrage_paper_accounts      ?? ??????
  锟� asterdex_book_ticker          ?? ????
  锟� asterdex_depth_snapshots      ?? 20 ???
  锟� logs/mm_lane_status.json      ?? worker ??(???????)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/hft", tags=["hft"])

_LANE_ID = "mm_asterdex"
_ROOT = Path(__file__).resolve().parents[2]
_HEARTBEAT = _ROOT / "logs" / "mm_lane_status.json"
DEFAULT_PAPER_BALANCE = 300.0

# 这些读接口被页面 2s 轮询打满后，会占住后端唯一的处理队列，别的页面切过去也要等数秒。
# 短缓存让重复请求只读内存，不再每次都扫账本 / 深度表。
_READ_CACHE: Dict[str, Any] = {}


def _read_cache_get(key: str, ttl: float) -> Any:
    hit = _READ_CACHE.get(key)
    if not hit:
        return None
    if time.time() - float(hit[0]) >= ttl:
        return None
    return hit[1]


def _read_cache_put(key: str, value: Any) -> Any:
    _READ_CACHE[key] = (time.time(), value)
    return value


# ??????????????????????????????????????????????????????????????????????
# ????
# ??????????????????????????????????????????????????????????????????????
def _lane_meta() -> Dict[str, Any]:
    from backend.services import lane_registry as reg
    lane = reg.get_lane(_LANE_ID) or {}
    return dict(lane.get("meta") or {})


def _symbols() -> List[str]:
    m = _lane_meta()
    return [str(s) for s in (m.get("symbols") or []) if str(s)]


def _account_row(account_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not account_id:
        return None
    from sqlalchemy import text as sa_text
    from backend.database.connection import SessionLocal
    try:
        with SessionLocal() as db:
            r = db.execute(sa_text(
                "SELECT id, name, total_equity, available_balance, frozen_balance,"
                " realized_pnl, status, updated_at FROM arbitrage_paper_accounts"
                " WHERE id=:i"), {"i": int(account_id)}).fetchone()
        if not r:
            return None
        return {"id": int(r[0]), "name": r[1],
                "total_equity": float(r[2]) if r[2] is not None else None,
                "available_balance": float(r[3]) if r[3] is not None else None,
                "frozen_balance": float(r[4]) if r[4] is not None else None,
                "realized_pnl": float(r[5]) if r[5] is not None else None,
                "status": r[6], "updated_at": str(r[7]) if r[7] is not None else None}
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] ???????: %s", e)
        return None


def _fee_since(since: Optional[str]) -> Dict[str, Any]:
    """Fee USD from lane_ledger. fee_bp is negative; usd = fee_bp * notional / 10000.
    Since-reset uses account_reset_at. Lifetime is never cleared by reset.
    """
    cache_key = "fee:" + (since or "")
    cached = _read_cache_get(cache_key, 15.0)
    if cached is not None:
        return cached
    from sqlalchemy import text as sa_text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    out = {
        "fee_usd": 0.0, "fee_fills": 0, "fee_gross_usd": 0.0,
        "fee_lifetime_usd": 0.0, "fee_lifetime_fills": 0,
    }
    since_sql = ""
    params: Dict[str, Any] = {"l": _LANE_ID}
    if since:
        since_sql = " AND ts >= CAST(:since AS timestamptz)"
        params["since"] = since
    try:
        with system_identity():
            with SessionLocal() as db:
                r = db.execute(sa_text(
                    "SELECT COALESCE(SUM(fee_bp*notional)/10000.0,0),"
                    " COUNT(*) FILTER (WHERE fee_bp < -0.01),"
                    " COALESCE(SUM(-4.0*notional)/10000.0,0) "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    + since_sql
                ), params).fetchone()
                life = db.execute(sa_text(
                    "SELECT COALESCE(SUM(fee_bp*notional)/10000.0,0),"
                    " COUNT(*) FILTER (WHERE fee_bp < -0.01) "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                ), {"l": _LANE_ID}).fetchone()
        out["fee_usd"] = round(float(r[0] or 0.0), 4)
        out["fee_fills"] = int(r[1] or 0)
        out["fee_gross_usd"] = round(float(r[2] or 0.0), 4)
        out["fee_lifetime_usd"] = round(float(life[0] or 0.0), 4)
        out["fee_lifetime_fills"] = int(life[1] or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] fee sum failed: %s", e)
    return _read_cache_put(cache_key, out)


def _opt_ts() -> Optional[str]:
    """??????????scout ????? ts??? meta ???????"""
    try:
        p = _ROOT / "data" / "universe_optimizer.json"
        if p.exists():
            return str(json.loads(p.read_text(encoding="utf-8")).get("ts") or "") or None
    except Exception:
        pass
    return None


def _universe_as_of() -> Optional[str]:
    """[2026-10-10 修] PP 时代宇宙由 worker 雷达管理：as_of 必须来自心跳的
    universe_radar.as_of（epoch → 本地 ISO）。此前 overview 直接报
    data/universe_optimizer.json 的 ts —— 旧 AI 选币链产物，PP 时代不再更新，
    页面「上次 11:38 / 下次 0:54」因此永远对不上真实节奏。"""
    try:
        hb = _heartbeat()
        rad = hb.get("universe_radar") or {}
        as_of = rad.get("as_of")
        if as_of:
            return datetime.fromtimestamp(float(as_of)).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        pass
    return _opt_ts()


def _heartbeat() -> Dict[str, Any]:
    """? worker ??(???????)??? ? {}?"""
    try:
        if _HEARTBEAT.exists():
            return json.loads(_HEARTBEAT.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _states() -> Dict[str, Any]:
    """?????(???? states ??)?"""
    return dict((_heartbeat().get("states") or {}))


def _live_mids(symbols: List[str]) -> Dict[str, float]:
    """???????(??)????? quote_mid,?? book_ticker ???"""
    out: Dict[str, float] = {}
    hb = _heartbeat()
    hb_states = hb.get("states") or {}
    for s in symbols:
        st = hb_states.get(s) or {}
        qm = float(st.get("quote_mid") or 0.0)
        if qm > 0:
            out[s] = qm
    # ??:book_ticker ?? mid
    missing = [s for s in symbols if s not in out]
    if missing:
        try:
            from sqlalchemy import text as sa_text
            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                for s in missing:
                    vs = s if s.endswith(("USDT", "USD1")) else f"{s}USDT"
                    r = db.execute(sa_text(
                        "SELECT bid_px, ask_px FROM asterdex_book_ticker"
                        " WHERE symbol=:s AND bid_px>0 ORDER BY event_ts_ms DESC"
                        " LIMIT 1"), {"s": vs}).fetchone()
                    if r and r[0] and r[1]:
                        out[s] = (float(r[0]) + float(r[1])) / 2.0
        except Exception as e:  # noqa: BLE001
            logger.warning("[hft] ??? mid ??: %s", e)
    return out


# ??????????????????????????????????????????????????????????????????????
# ??
# ??????????????????????????????????????????????????????????????????????
@router.get("/ping")
def hft_ping() -> Dict[str, Any]:
    return {"ok": True, "lane_id": _LANE_ID,
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.get("/overview")
def hft_overview() -> Dict[str, Any]:
    """??:?? + ?? + ?????"""
    m = _lane_meta()
    symbols = _symbols()
    hb = _heartbeat()
    # ????????? + ????????????????
    # ????????? state ??? ofi ?????????
    states = _states()
    trend: Dict[str, Any] = {}
    try:
        from sqlalchemy import text as sa_text
        from backend.database.connection import MarketSessionLocal
        from backend.services.market_maker.trend_score import trend_score as _ts
        now_ms = time.time() * 1000
        with MarketSessionLocal() as db:
            for s in symbols:
                vs = s if s.endswith(("USDT", "USD1")) else f"{s}USDT"
                r = db.execute(sa_text(
                    "SELECT "
                    " COALESCE(SUM(qty) FILTER (WHERE is_buyer_maker IS FALSE),0),"
                    " COALESCE(SUM(qty) FILTER (WHERE is_buyer_maker IS TRUE),0)"
                    " FROM asterdex_trades WHERE symbol=:s AND event_ts_ms > :t"
                ), {"s": vs, "t": now_ms - 300_000}).fetchone()
                bv, sv = float(r[0] or 0), float(r[1] or 0)
                tot = bv + sv
                ofi = ((bv - sv) / tot) if tot > 0 else None
                # ??:?????(??????),???????(?????)?
                res = _ts(ofi, None, None)
                trend[s] = {"score": res["score"],
                            "dir": ("up" if res["dir"] > 0 else "down" if res["dir"] < 0 else "flat")}
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] overview trend failed: %s", e)
        trend = {}
    return {
        "module": "hft_swing",
        "lane_id": _LANE_ID,
        "mode": m.get("_mode") or m.get("mode") or "paper",
        "status": m.get("_status") or m.get("status") or "active",
        "strategy_type": m.get("strategy_type") or "MM",
        "venue": m.get("venue") or "asterdex",
        "symbols": symbols,
        "trend": trend,
        "fixed": list((m.get("universe") or {}).get("fixed") or []),
        "ai": list((m.get("universe") or {}).get("ai") or []),
        "universe_source": m.get("universe_source") or "universe_optimizer",
        "universe_as_of": _universe_as_of(),
        "depth_in_universe": symbols,
        "depth_symbols_count": len(symbols),
        "depth_configured_count": len(symbols),
        "ticks": hb.get("ticks"), "fills": hb.get("fills"),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _depth_ladder(symbols: List[str], depth: int) -> Dict[str, Dict[str, Any]]:
    """? asterdex_depth_snapshots ???????? 20 ????

    ?? {??: {bids, asks, ts_ms, age_ms}}? bids/asks ? [?,?,???]?
    bids ????????????asks ?????????
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return out
    try:
        from sqlalchemy import text as sa_text
        from backend.database.connection import MarketSessionLocal
        now_ms = time.time() * 1000
        with MarketSessionLocal() as db:
            for s in symbols:
                vs = s if s.endswith(("USDT", "USD1")) else f"{s}USDT"
                r = db.execute(sa_text(
                    "SELECT bids, asks, event_ts_ms FROM asterdex_depth_snapshots"
                    " WHERE symbol=:s ORDER BY event_ts_ms DESC LIMIT 1"
                ), {"s": vs}).fetchone()
                if not r or r[2] is None:
                    continue
                bids_raw = r[0] or []
                asks_raw = r[1] or []
                if isinstance(bids_raw, str):
                    bids_raw = json.loads(bids_raw)
                if isinstance(asks_raw, str):
                    asks_raw = json.loads(asks_raw)
                def _lv(rows, reverse):
                    rows = sorted(
                        ([float(p), float(q)] for p, q in (rows or [])),
                        key=lambda x: x[0], reverse=reverse)[: int(depth)]
                    cum = 0.0
                    out_rows = []
                    for p, q in rows:
                        cum += q
                        out_rows.append([p, q, cum])
                    return out_rows
                ts_ms = float(r[2])
                out[s] = {
                    "bids": _lv(bids_raw, True),
                    "asks": _lv(asks_raw, False),
                    "levels": int(depth),
                    "ts_ms": ts_ms,
                    "age_ms": max(0.0, now_ms - ts_ms),
                }
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] depth ladder failed: %s", e)
    return out


def _recent_fills_by_symbol(symbols: List[str], per_symbol: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """???? N ???(???????)?"""
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in symbols}
    if not symbols:
        return out
    try:
        from sqlalchemy import text as sa_text
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        with system_identity():
            with SessionLocal() as db:
                rs = db.execute(sa_text(
                    "SELECT symbol, ts, meta_json->>'side' side, notional, net_bp,"
                    " meta_json->>'exit_path' path FROM lane_ledger"
                    " WHERE lane_id=:l AND event='fill' AND symbol = ANY(:syms)"
                    " ORDER BY ts DESC LIMIT 200"),
                    {"l": _LANE_ID, "syms": symbols}).fetchall()
        for sym, ts, side, noti, net, path in rs:
            if sym not in out or len(out[sym]) >= per_symbol:
                continue
            out[sym].append({
                "ts": str(ts), "side": side, "price": None,
                "qty": None, "net_bp": float(net or 0),
                "notional_usd": float(noti or 0),
                "is_close": bool(path),
            })
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] recent_fills failed: %s", e)
    return out


@router.get("/board")
def hft_board(symbols: Optional[str] = None, depth: int = 20) -> Dict[str, Any]:
    """????:??? ???? + ???? + ?? + ?????"""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] \
        if symbols else _symbols()
    cache_key = "board:" + ",".join(syms) + ":" + str(int(depth))
    cached = _read_cache_get(cache_key, 2.0)
    if cached is not None:
        return cached
    states = _states()
    mids = _live_mids(syms)
    ladders = _depth_ladder(syms, depth)
    recent = _recent_fills_by_symbol(syms)
    cards = []
    for s in syms:
        st = states.get(s) or {}
        mid = mids.get(s)
        lad = ladders.get(s)
        qb = float(st.get("quote_bid") or 0.0)
        qa = float(st.get("quote_ask") or 0.0)
        qty = float(st.get("qty") or 0.0)
        avg_mid = float(st.get("avg_mid") or 0.0)
        opened = float(st.get("opened_ts") or 0.0)
        # ?? = ?? 锟� (?? ? ???)?????????????????
        unreal = None
        if abs(qty) > 1e-12 and avg_mid > 0 and mid:
            unreal = round(qty * (mid - avg_mid), 4)
        hold_ms = int((time.time() - opened) * 1000) if opened > 1_000_000_000 else None
        # ?????? + ??
        top = None
        spread_bp = None
        if lad and lad["bids"] and lad["asks"]:
            bb = lad["bids"][0]
            aa = lad["asks"][0]
            top = {"bid": bb[0], "bid_qty": bb[1], "ask": aa[0], "ask_qty": aa[1]}
            if bb[0] > 0:
                spread_bp = round((aa[0] - bb[0]) / bb[0] * 1e4, 3)
        # ?? ??:??? + ?????(?????) ??
        # ??? = ?????????? bp
        bid_width = ask_width = None
        if mid and mid > 0:
            if qb > 0:
                bid_width = round((mid - qb) / mid * 1e4, 2)
            if qa > 0:
                ask_width = round((qa - mid) / mid * 1e4, 2)
        # ????? = ?????**??**(???)???? USD?
        # ??:???? > ????????;??:???? < ?????????
        bid_q_ahead = ask_q_ahead = None
        if lad:
            if qb > 0:
                ahead = sum(p * q for p, q, _c in lad["bids"] if p > qb)
                bid_q_ahead = round(ahead, 2)
            if qa > 0:
                ahead = sum(p * q for p, q, _c in lad["asks"] if p < qa)
                ask_q_ahead = round(ahead, 2)
        cards.append({
            "symbol": s,
            "has_depth": lad is not None, "depth_expected": True,
            "depth_age_ms": (lad["age_ms"] if lad else None),
            "mid": mid, "mid_is_live": mid is not None,
            "ref_mid": float(st.get("quote_mid") or 0.0) or None,
            "top": top,
            "top_age_ms": (lad["age_ms"] if lad else None),
            "spread_bp": spread_bp,
            "ladder": ({"bids": lad["bids"], "asks": lad["asks"],
                        "levels": lad["levels"], "ts_ms": lad["ts_ms"]} if lad else None),
            "mine": {
                "bid": qb or None, "ask": qa or None,
                "bid_width_bp": bid_width, "ask_width_bp": ask_width,
                "bid_queue_ahead_usd": bid_q_ahead, "ask_queue_ahead_usd": ask_q_ahead,
                "quoted_age_ms": (int((time.time() - float(st.get("quote_ts") or 0)) * 1000)
                                  if float(st.get("quote_ts") or 0) > 1e9 else None),
            },
            "position": {
                "qty": qty,
                "avg_px": float(st.get("avg_px") or 0.0) or None,
                "avg_mid": avg_mid or None,
                "opened_ts": float(st.get("opened_ts") or 0.0) or None,
                "last_ts": float(st.get("quote_ts") or 0.0) or None,
                "unrealized_usd": unreal,
                "hold_ms": hold_ms,
            },
            "recent_fills": recent.get(s, []),
        })
    return _read_cache_put(cache_key, {
        "module": "hft_swing", "cards": cards, "state_age_ms": 0,
        "state_source": "heartbeat",
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/account")
def hft_account() -> Dict[str, Any]:
    """???? + ?????"""
    cached = _read_cache_get("account", 3.0)
    if cached is not None:
        return cached
    m = _lane_meta()
    acct_id = m.get("paper_account_id")
    acct = _account_row(acct_id)
    hb = _heartbeat()
    states = _states()
    open_pos = []
    mids = _live_mids([s for s, st in states.items()
                       if abs(float(st.get("qty") or 0.0)) > 1e-12])
    unrealized = 0.0
    for sym, st in states.items():
        q = float(st.get("qty") or 0.0)
        if abs(q) <= 1e-12:
            continue
        mid = mids.get(sym) or 0.0
        am = float(st.get("avg_mid") or 0.0)
        u = 0.0
        if mid > 0 and am > 0:
            u = q * (mid - am)
        unrealized += u
        open_pos.append({"symbol": sym, "qty": q, "avg_mid": am, "mid": mid,
                         "unrealized_usd": round(u, 4)})
    realized = float(acct["realized_pnl"]) if acct and acct["realized_pnl"] is not None else 0.0
    since = m.get("account_reset_at") or m.get("stats_since")
    fees = _fee_since(str(since) if since else None)
    # ????? total_equity ???????????? + ?? + ?????
    frozen = float(acct["frozen_balance"] or 0.0) if acct else 0.0
    avail = acct.get("available_balance") if acct else None
    live_equity = None
    if avail is not None:
        live_equity = round(float(avail) + frozen + unrealized, 4)
    payload = {
        "module": "hft_swing", "lane_id": _LANE_ID,
        "mode": m.get("_mode") or "paper", "status": m.get("_status") or "active",
        "strategy_type": m.get("strategy_type") or "MM",
        "venue": m.get("venue") or "asterdex",
        "paper": {
            "account_id": acct_id, "default_balance": DEFAULT_PAPER_BALANCE,
            "shadow_equity": float(m.get("shadow_equity") or 0.0),
            "unrealized_usd": round(unrealized, 4),
            "realized_usd": realized,
            "total_pnl_usd": round(realized + unrealized, 4),
            "open_positions": open_pos,
            "fee_usd": fees["fee_usd"],
            "fee_fills": fees["fee_fills"],
            "fee_gross_usd": fees["fee_gross_usd"],
            "fee_lifetime_usd": fees["fee_lifetime_usd"],
            "fee_lifetime_fills": fees["fee_lifetime_fills"],
            "name": acct.get("name") if acct else None,
            "total_equity": live_equity if live_equity is not None else (acct.get("total_equity") if acct else None),
            "total_equity_source": "available+frozen+upnl" if live_equity is not None else None,
            "available_balance": acct.get("available_balance") if acct else None,
            "realized_pnl": realized,
            "account_status": acct.get("status") if acct else None,
            "updated_at": acct.get("updated_at") if acct else None,
        },
        "live": {"credentials": 0, "enabled_credentials": 0, "ready": False,
                 "reason": "??????????"},
        "sizing": {
            "compound_ratio": float((m.get("params") or {}).get("compound_ratio") or 0.0),
            "fill_notional": float((m.get("params") or {}).get("fill_notional") or 0.0),
            "min_notional_usd": 5.0,
        },
        "direction_log": (hb.get("direction_card") or {}).get("log") or [],
        "direction_state": hb.get("direction_card") or {},
        "direction_score": hb.get("dir_score") or {},
        "events": hb.get("events") or [],
        "skip_counts": hb.get("skip_counts") or {},
        "venue_filters": hb.get("venue_filters") or {},
        "q_speed": hb.get("q_speed") or {},
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    return _read_cache_put("account", payload)


def _live_equity() -> Optional[float]:
    """?? = ?? + ?? + ?????????? total_equity ??????"""
    m = _lane_meta()
    acct = _account_row(m.get("paper_account_id"))
    if not acct or acct.get("available_balance") is None:
        return None
    states = _states()
    mids = _live_mids([s for s, st in states.items()
                       if abs(float(st.get("qty") or 0.0)) > 1e-12])
    unreal = 0.0
    for sym, st in states.items():
        q = float(st.get("qty") or 0.0)
        if abs(q) <= 1e-12:
            continue
        mid = mids.get(sym) or 0.0
        am = float(st.get("avg_mid") or 0.0)
        if mid > 0 and am > 0:
            unreal += q * (mid - am)
    return round(float(acct["available_balance"])
                 + float(acct.get("frozen_balance") or 0.0) + unreal, 4)


def _realized_cached() -> float:
    """账户 realized_pnl（自上次重置的总盈亏），15s 短缓存——PK 查询很快，
    但 1s 轮询下没必要每秒都打库。"""
    cached = _read_cache_get("realized", 15.0)
    if cached is not None:
        return cached
    m = _lane_meta()
    acct = _account_row(m.get("paper_account_id"))
    val = float(acct["realized_pnl"]) if acct and acct["realized_pnl"] is not None else 0.0
    return _read_cache_put("realized", val)


@router.get("/hero")
def hft_hero() -> Dict[str, Any]:
    """今日战绩大字区：今日盈亏/权益/胜率/盈亏比（1s 心跳快路径 + SQL 兜底）。"""
    cached = _read_cache_get("hero", 1.0)
    if cached is not None:
        return cached
    m = _lane_meta()
    since = m.get("account_reset_at") or m.get("stats_since")
    # ══ [2026-10-10 用户:数值必须 1 秒刷新] 心跳快路径 ════════════════
    # worker 每 tick(1s) 把 权益 + 今日腿级统计 写进 mm_lane_status.json；
    # 这里直接读文件 ⇒ 页面数值与抓取同频，零 SQL。文件缺失/过期(<6s 判新鲜)
    # ⇒ 回落到下面的 SQL 口径（结果不变，只是慢）。
    _snap: Optional[Dict[str, Any]] = None
    try:
        _snap = json.loads(_HEARTBEAT.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        _snap = None
    _snap_fresh = bool(_snap) and (time.time() - float(_snap.get("ts") or 0.0)) < 6.0
    if _snap_fresh:
        tl = _snap.get("today_leg") or {}
        today_n = int(tl.get("n") or 0)
        wins = int(tl.get("wins") or 0)
        today_pnl = float(tl.get("pnl_usd") or 0.0)
        avg_win = float(tl.get("avg_win_bp") or 0.0)
        avg_loss = float(tl.get("avg_loss_bp") or 0.0)
        win_rate = (wins / today_n) if today_n else None
        pl_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
        # 零 DB 快路径：权益直接用 worker 心跳值（available+frozen 口径，
        # 与页面一致；持仓时浮动盈亏差异 <1 美分/腿，可接受）。
        equity = float(_snap.get("equity") or 0.0) or None
        realized = _realized_cached()
        fees = _fee_since(str(since) if since else None)   # 内部 15s 缓存，慢查询不会高频触发
        return _read_cache_put("hero", {
            "equity": equity, "today_pnl_usd": round(today_pnl, 4),
            "today_fills": today_n,
            "win_rate": (round(win_rate, 4) if win_rate is not None else None),
            "pl_ratio": (round(pl_ratio, 3) if pl_ratio is not None else None),
            "avg_win_bp": round(avg_win, 3), "avg_loss_bp": round(avg_loss, 3),
            "total_pnl_usd": round(realized, 4),
            "fee_lifetime_usd": fees["fee_usd"],
            "worker_alive": True,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })
    from sqlalchemy import text as sa_text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    equity = _live_equity()
    today_n = wins = 0
    today_pnl = avg_win = avg_loss = 0.0
    try:
        with system_identity():
            with SessionLocal() as db:
                # ?? = ???????????????????????????
                sql = (
                    "SELECT COUNT(*) n,"
                    " COALESCE(SUM(net_bp*notional)/10000.0,0) pnl,"
                    " COALESCE(AVG(net_bp) FILTER (WHERE net_bp>0),0) aw,"
                    " COALESCE(AVG(net_bp) FILTER (WHERE net_bp<=0),0) al,"
                    " COUNT(*) FILTER (WHERE net_bp>0) nw "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts >= (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')"
                    "            AT TIME ZONE 'Asia/Shanghai')"
                )
                params: Dict[str, Any] = {"l": _LANE_ID}
                if since:
                    sql += " AND ts >= CAST(:since AS timestamptz)"
                    params["since"] = str(since)
                r = db.execute(sa_text(sql), params).fetchone()
        today_n = int(r[0] or 0)
        today_pnl = float(r[1] or 0.0)
        avg_win = float(r[2] or 0.0)
        avg_loss = float(r[3] or 0.0)
        wins = int(r[4] or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] hero ????: %s", e)
    win_rate = (wins / today_n) if today_n else None
    pl_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    worker_alive = False
    try:
        hb = _heartbeat()
        worker_alive = bool(hb) and (time.time() - float(hb.get("ts") or 0.0)) < 30.0
    except Exception:
        pass
    # ??? = ???? ? ???????? = ??????????
    # ??? / ???????????????????????????????
    acct = _account_row(m.get("paper_account_id"))
    realized = float(acct["realized_pnl"]) if acct and acct["realized_pnl"] is not None else 0.0
    fees = _fee_since(str(since) if since else None)
    total_pnl = round(realized, 4)
    fee_lifetime = fees["fee_usd"]
    return _read_cache_put("hero", {
        "equity": equity, "today_pnl_usd": round(today_pnl, 4),
        "today_fills": today_n,
        "win_rate": (round(win_rate, 4) if win_rate is not None else None),
        "pl_ratio": (round(pl_ratio, 3) if pl_ratio is not None else None),
        "avg_win_bp": round(avg_win, 3), "avg_loss_bp": round(avg_loss, 3),
        "total_pnl_usd": total_pnl,
        "fee_lifetime_usd": fee_lifetime,
        "worker_alive": worker_alive,
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/fills")
def hft_fills(limit: int = 50, hours: float = 24.0) -> Dict[str, Any]:
    """成交记录（六维账本）：
    - `items`：最近 `limit` 笔明细（表格用）；
    - `summary`：**完整 24h 窗口**的汇总（卡片头部用）——此前后端不返回
      summary，前端只能用列表里那 50 行凑数，「近 24h 成交 50 笔」就是这么
      来的（实际今天就有 290+ 笔）。加权 bp = 金额 ÷ 名义 × 1e4。
    """
    from sqlalchemy import text as sa_text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    rows = []
    summary: Dict[str, Any] = {}
    try:
        with system_identity():
            with SessionLocal() as db:
                rs = db.execute(sa_text(
                    "SELECT ts, symbol, notional, net_bp, spread_bp, price_bp,"
                    " fee_bp, slippage_bp, meta_json->>'exit_path' path,"
                    " meta_json->>'side' side FROM lane_ledger"
                    " WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - make_interval(hours => :h)"
                    " ORDER BY ts DESC LIMIT :n"),
                    {"l": _LANE_ID, "h": int(hours), "n": int(limit)}).fetchall()
                # [2026-10-10] 全窗口汇总（与列表行数无关）
                sr = db.execute(sa_text(
                    "SELECT COUNT(*),"
                    " COALESCE(SUM(notional),0),"
                    " COALESCE(SUM(net_bp*notional)/10000.0,0),"
                    " COALESCE(SUM(net_bp),0),"
                    " COALESCE(SUM(spread_bp*notional)/10000.0,0),"
                    " COALESCE(SUM(price_bp*notional)/10000.0,0),"
                    " COALESCE(SUM(fee_bp*notional)/10000.0,0),"
                    " COALESCE(SUM(slippage_bp*notional)/10000.0,0)"
                    " FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - make_interval(hours => :h)"),
                    {"l": _LANE_ID, "h": int(hours)}).fetchone()
                if sr:
                    _fills, _noti, _netusd, _netbps, _sprusd, _priusd, _feeusd, _slusd = sr
                    _noti_f = float(_noti or 0.0)
                    summary = {
                        "fills": int(_fills or 0),
                        "notional_sum": round(_noti_f, 4),
                        "net_usd_sum": round(float(_netusd or 0.0), 4),
                        "net_bp_sum": round(float(_netbps or 0.0), 4),
                        "spread_usd_sum": round(float(_sprusd or 0.0), 4),
                        "price_usd_sum": round(float(_priusd or 0.0), 4),
                        "fee_usd_sum": round(float(_feeusd or 0.0), 4),
                        "slippage_usd_sum": round(float(_slusd or 0.0), 4),
                        "net_bp_w": (round(float(_netusd or 0.0) / _noti_f * 1e4, 3)
                                     if _noti_f > 0 else None),
                        "spread_bp_w": (round(float(_sprusd or 0.0) / _noti_f * 1e4, 3)
                                        if _noti_f > 0 else None),
                        "price_bp_w": (round(float(_priusd or 0.0) / _noti_f * 1e4, 3)
                                       if _noti_f > 0 else None),
                        "fee_bp_w": (round(float(_feeusd or 0.0) / _noti_f * 1e4, 3)
                                     if _noti_f > 0 else None),
                    }
        for ts, sym, noti, net, spr, pri, fee, sli, path, side in rs:
            noti_f = float(noti or 0)
            rows.append({
                "ts": str(ts), "symbol": sym, "side": side,
                "notional": noti_f, "notional_usd": noti_f,
                "net_bp": float(net or 0),
                "spread_bp": float(spr or 0), "price_bp": float(pri or 0),
                "fee_bp": float(fee or 0), "slippage_bp": float(sli or 0),
                "funding_bp": 0.0,
                "net_usd": float(net or 0) / 1e4 * noti_f,
                "exit_path": path, "event": "fill",
            })
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] fills failed: %s", e)
    return {"module": "hft_swing", "lane_id": _LANE_ID, "hours": hours,
            "items": rows, "fills": rows, "count": len(rows),
            "summary": summary,
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.get("/config")
def hft_config() -> Dict[str, Any]:
    """?????????(??)?"""
    m = _lane_meta()
    params = dict(m.get("params") or {})
    return {"module": "hft_swing", "lane_id": _LANE_ID, "params": params,
            "notes": {}, "enum_labels": {}, "limits": {},
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.get("/universe/live")
def hft_universe_live() -> Dict[str, Any]:
    """??????:? 24h ????/? bp/???"""
    from sqlalchemy import text as sa_text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    rows = []
    totals = {"legs": 0, "net_bp": 0.0, "net_usd": 0.0}
    try:
        with system_identity():
            with SessionLocal() as db:
                rs = db.execute(sa_text(
                    "SELECT symbol, COUNT(*) n,"
                    " SUM(net_bp*notional)/NULLIF(SUM(notional),0) avg_bp,"
                    " SUM(net_bp*notional)/10000.0 pnl, SUM(notional) noti,"
                    " SUM(price_bp*notional)/10000.0 price_usd,"
                    " SUM(spread_bp*notional)/10000.0 spread_usd "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - INTERVAL '24 hours' GROUP BY 1 ORDER BY 4 DESC"),
                    {"l": _LANE_ID}).fetchall()
        tot_n = tot_bp = 0.0
        for sym, n, abp, pnl, noti, price_usd, spread_usd in rs:
            legs = int(n or 0)
            pnl_f = float(pnl or 0.0)
            rows.append({"symbol": sym, "legs": legs,
                         "net_bp": round(float(abp or 0), 3),
                         "net_bp_per_leg": round(float(abp or 0), 3),
                         "legs_per_hour": round(legs / 24.0, 1),
                         "net_usd": round(pnl_f, 3),
                         "price_usd": round(float(price_usd or 0), 3),
                         "spread_usd": round(float(spread_usd or 0), 3),
                         "hint": (u"\u4fdd\u7559" if pnl_f >= 0 else u"\u6438\u9664\u5019\u9009")})
            tot_n += legs
            tot_bp += float(abp or 0) * float(noti or 0)
        totals = {"legs": tot_n,
                  "net_bp": round(tot_bp / 1e4, 3) if tot_n else 0.0,
                  "net_usd": round(sum(r["net_usd"] for r in rows), 3)}
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] universe/live ??: %s", e)
    return {"module": "hft_swing", "rows": rows, "totals": totals,
            "hours_elapsed": 24, "note": "? 24h ??????",
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.get("/evolution")
def hft_evolution() -> Dict[str, Any]:
    """??????(????????)?"""
    gov = {}
    try:
        p = _ROOT / "data" / "evolution_governor_state.json"
        if p.exists():
            gov = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    laws = 0
    try:
        pb = _ROOT / "data" / "evolution_governor_playbook.json"
        if pb.exists():
            laws = len(json.loads(pb.read_text(encoding="utf-8")).get("laws") or [])
    except Exception:
        pass
    # ????(?14?,? lane_ledger ?) + ??????
    daily: List[Dict[str, Any]] = []
    streak = 0
    try:
        from sqlalchemy import text as sa_text
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        with system_identity():
            with SessionLocal() as db:
                rs = db.execute(sa_text(
                    "SELECT date_trunc('day', ts AT TIME ZONE 'Asia/Shanghai') d,"
                    " COUNT(*) legs,"
                    " SUM(net_bp*notional)/10000.0 net_usd,"
                    " SUM(net_bp*notional)/NULLIF(SUM(notional),0) bp_per_leg "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - INTERVAL '14 days'"
                    " GROUP BY 1 ORDER BY 1"),
                    {"l": _LANE_ID}).fetchall()
        for d, legs, net_usd, bpl in rs:
            daily.append({
                "date": d.strftime("%Y-%m-%d"),
                "legs": int(legs or 0),
                "net_usd": round(float(net_usd or 0), 3),
                "net_bp_per_leg": round(float(bpl or 0), 3),
                "active_hours": None,
            })
        # ????(????????)
        for row in reversed(daily):
            if row["net_usd"] > 0:
                streak += 1
            else:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] evolution daily failed: %s", e)
    # markout KPI(? 24h ????/???)
    markout = {"n": None, "markout_bp": None, "capture_bp": None,
               "adverse_capture_ratio": None, "verdict": None, "ts": None}
    try:
        from sqlalchemy import text as sa_text
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        with system_identity():
            with SessionLocal() as db:
                r = db.execute(sa_text(
                    "SELECT COUNT(*) n,"
                    " AVG(price_bp) markout,"
                    " AVG(spread_bp) capture,"
                    " SUM(CASE WHEN price_bp<0 THEN -price_bp ELSE 0 END)"
                    " /NULLIF(SUM(ABS(spread_bp)),0) adverse_ratio "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - INTERVAL '24 hours'"),
                    {"l": _LANE_ID}).fetchone()
        if r and r[0]:
            n = int(r[0])
            mk = float(r[1] or 0)
            cap = float(r[2] or 0)
            ar = float(r[3]) if r[3] is not None else None
            verdict = None
            if ar is not None:
                verdict = ("healthy" if ar < 1.0 else
                           "warn" if ar < 2.0 else "adverse")
            markout = {"n": n, "markout_bp": round(mk, 3),
                       "capture_bp": round(cap, 3),
                       "adverse_capture_ratio": (round(ar, 3) if ar is not None else None),
                       "verdict": verdict,
                       "ts": time.time()}
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] evolution markout failed: %s", e)
    # [2026-10-10 学习进化前端升级] 这两块此前是**硬编码空数组** ⇒ 前端自进化条
    # 永远显示「闸门提案 0 / 待判 0」，读者无法区分"没有问题"与"从未接线"。
    # 现在：待判读 `logs/self_tuner_pending.json`（真实待判队列），
    # 顾问提案读 `data/lane_advisor_journal.jsonl`（模型每轮的结论）。
    pending_verdicts: List[Dict[str, Any]] = []
    try:
        p = _ROOT / "logs" / "self_tuner_pending.json"
        if p.exists():
            rows = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    pending_verdicts.append({
                        "param": r.get("param"),
                        "old": r.get("old"),
                        "new": r.get("new"),
                        "rollback": r.get("rollback"),
                        "applied_at": r.get("applied_at"),
                        "verdict_at": r.get("verdict_at"),
                        "verdict_metric": r.get("verdict_metric"),
                        "reason": str(r.get("reason") or "")[:200],
                    })
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] pending_verdicts failed: %s", e)

    advisor_recent: List[Dict[str, Any]] = []
    try:
        jp = _ROOT / "data" / "lane_advisor_journal.jsonl"
        if jp.exists():
            lines = jp.read_text(encoding="utf-8").splitlines()[-5:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                advisor_recent.append({
                    "ts": r.get("ts"),
                    "lane": r.get("lane"),
                    "decision": r.get("decision"),
                    "param": r.get("param"),
                    "new": r.get("new"),
                    "cited": r.get("cited") or [],
                    "why": str(r.get("why") or "")[:200],
                })
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] advisor_recent failed: %s", e)

    return {"module": "hft_swing", "lane_id": _LANE_ID,
            "streak_positive_days": streak, "daily": daily, "gate_proposals": [],
            "bandit": {"current": [], "proposed": [], "new_in": [], "dropped": [],
                       "mode": None},
            "surface": {"ts": None, "per_symbol": [], "net_band": {}, "processed": False},
            "pending_verdicts": pending_verdicts, "playbook_laws": laws,
            # [2026-10-10] 空壳字段显式列出：前端据此显示「未接线」而不是「0」
            "stub_fields": ["gate_proposals", "bandit", "surface"],
            "advisor_recent": advisor_recent,
            "markout_kpi": markout,
            "lane_pause": {"counts": {}, "last": None},
            "governor": gov,
            "as_of": datetime.now(timezone.utc).isoformat()}


def _reconcile(realized_end: float) -> Dict[str, Any]:
    """??:?????? vs ??? realized_pnl??? ? ok=True?"""
    try:
        m = _lane_meta()
        acct = _account_row(m.get("paper_account_id"))
        acct_realized = (float(acct["realized_pnl"])
                         if acct and acct["realized_pnl"] is not None else None)
        if acct_realized is None:
            return {"realized_plus_floating": realized_end,
                    "balance_total_equity": None, "diff": None, "ok": None,
                    "note": "????,????"}
        diff = round(float(realized_end) - acct_realized, 4)
        return {"realized_plus_floating": realized_end,
                "balance_total_equity": acct_realized, "diff": diff,
                "ok": abs(diff) < 0.5,
                "note": "???? vs ?? realized_pnl"}
    except Exception as e:  # noqa: BLE001
        return {"realized_plus_floating": realized_end,
                "balance_total_equity": None, "diff": None, "ok": None,
                "note": f"????: {e}"}


@router.get("/equity-series")
def hft_equity_series(days: int = 30) -> Dict[str, Any]:
    """??????(lane_ledger ?? net_bp璀秓tional ?????)?"""
    from sqlalchemy import text as sa_text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    points = []
    try:
        with system_identity():
            with SessionLocal() as db:
                rs = db.execute(sa_text(
                    "SELECT ts, net_bp*notional/10000.0 pnl FROM lane_ledger"
                    " WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - make_interval(days => :d)"
                    " ORDER BY ts"), {"l": _LANE_ID, "d": int(days)}).fetchall()
        cum = 0.0
        for ts, pnl in rs:
            cum += float(pnl or 0.0)
            points.append({"t": ts.timestamp(), "v": round(cum, 4)})
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] equity-series ??: %s", e)
    vals = [p["v"] for p in points] or [0.0]
    peak = max(vals)
    mdd = round(max(0.0, peak - min(vals)), 4)
    return {
        "period": f"{days}d", "initial_balance": 0.0,
        "realized_end": vals[-1] if vals else 0.0, "floating_now": None,
        "equity_total_now": vals[-1] if vals else 0.0,
        "peak_equity": peak, "max_drawdown_usd": mdd, "max_drawdown_pct": None,
        "costs_in_window": {"fees": 0.0, "funding_net": None},
        "points": points, "account_id": None, "account_name": None,
        "account_created_at": None,
        "reconcile": _reconcile(vals[-1] if vals else 0.0),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


class _BalanceBody(BaseModel):
    balance: float = Field(DEFAULT_PAPER_BALANCE, gt=0, le=1_000_000)


@router.post("/account/reset")
def hft_account_reset(body: _BalanceBody) -> Dict[str, Any]:
    """????????(? runner.reset_account,???????)?"""
    from backend.services.market_maker.runner import get_runner
    r = get_runner(_LANE_ID)
    if r is None:
        return {"ok": False, "error": "?????,????"}
    try:
        res = r.reset_account(float(body.balance))
        return {"ok": True, "lane_id": _LANE_ID, "balance": float(body.balance),
                "result": res if isinstance(res, dict) else None,
                "as_of": datetime.now(timezone.utc).isoformat()}
    except Exception as e:  # noqa: BLE001
        logger.warning("[hft] ??????: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/control/status")
def hft_control_status(body: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"ok": True, "lane_id": _LANE_ID, "note": "???:??????",
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.post("/control/mode")
def hft_control_mode(body: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"ok": False, "note": "???:??????",
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.get("/live/status")
def hft_live_status() -> Dict[str, Any]:
    return {"lane_id": _LANE_ID, "exists": False, "mode": "paper",
            "status": "stopped", "keys_configured": False,
            "note": "???:?????", "as_of": datetime.now(timezone.utc).isoformat()}


# ══════════════════════════════════════════════════════════════════
# [2026-10-10 用户要求] 实盘凭证前端自助配置：随时调换、不经人手。
# 值只落 data/live_credentials.json（gitignored）+ 同步进 .env（进程重启后
# LiveBridge 的环境变量读取也能拿到）；GET 永远只回"有没有"，绝不回显。
# ══════════════════════════════════════════════════════════════════
_LIVE_CRED_FILE = _ROOT / "data" / "live_credentials.json"
_LIVE_CRED_KEYS = ("v3_user", "v3_signer", "v3_private_key", "api_key", "api_secret")
_ENV_NAMES = {
    "v3_user": "ASTERDEX_V3_USER", "v3_signer": "ASTERDEX_V3_SIGNER",
    "v3_private_key": "ASTERDEX_V3_PRIVATE_KEY",
    "api_key": "ASTERDEX_API_KEY", "api_secret": "ASTERDEX_API_SECRET",
}


def _load_live_creds() -> Dict[str, Any]:
    try:
        if _LIVE_CRED_FILE.exists():
            return dict(json.loads(_LIVE_CRED_FILE.read_text(encoding="utf-8")) or {})
    except Exception:  # noqa: BLE001
        pass
    return {}


def _sync_creds_to_env_file(creds: Dict[str, Any]) -> None:
    """凭证同步进 .env（替换/追加 ASTERDEX_* 五行；.env 本身 gitignored）。"""
    try:
        env_path = _ROOT / ".env"
        lines = (env_path.read_text(encoding="utf-8").splitlines()
                 if env_path.exists() else [])
        out = [ln for ln in lines
               if not ln.strip().startswith(("ASTERDEX_V3_", "ASTERDEX_API_"))]
        for k, v in creds.items():
            if v and k in _ENV_NAMES:
                out.append(f"{_ENV_NAMES[k]}={v}")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("[live-creds] .env 同步失败: %s", e)


@router.get("/live/credentials")
def hft_live_credentials() -> Dict[str, Any]:
    creds = _load_live_creds()
    return {
        "configured": {k: bool(creds.get(k)) for k in _LIVE_CRED_KEYS},
        "v3_ready": bool(creds.get("v3_user") and creds.get("v3_signer")
                         and creds.get("v3_private_key")),
        "v2_ready": bool(creds.get("api_key") and creds.get("api_secret")),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/live/credentials")
def hft_live_credentials_save(body: Dict[str, Any] = None) -> Dict[str, Any]:
    """保存实盘凭证。空串 = 保持原值；'__CLEAR__' = 清空该项。"""
    body = body or {}
    creds = _load_live_creds()
    for k in _LIVE_CRED_KEYS:
        v = str(body.get(k) or "").strip()
        if v == "__CLEAR__":
            creds.pop(k, None)
        elif v:
            creds[k] = v
    try:
        _LIVE_CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LIVE_CRED_FILE.write_text(json.dumps(creds, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": f"写入失败: {e}"}
    _sync_creds_to_env_file(creds)
    return {"ok": True,
            "configured": {k: bool(creds.get(k)) for k in _LIVE_CRED_KEYS},
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.post("/live/kill")
def hft_live_kill(body: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"ok": False, "note": "???:?????",
            "as_of": datetime.now(timezone.utc).isoformat()}


@router.post("/live/control")
def hft_live_control(body: Dict[str, Any] = None) -> Dict[str, Any]:
    return {"ok": False, "note": "???:?????",
            "as_of": datetime.now(timezone.utc).isoformat()}
