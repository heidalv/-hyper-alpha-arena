# -*- coding: utf-8 -*-
"""[F57] 六维归因账本（lane_ledger）—— 复合策略的收益分解事实源。

设计依据：《复合策略与交易系统全面改造设计_V2》§2.4

为什么必须有它：
  现有系统把 P&L 混在一起记（`paper_orders.pnl` / `rebate_trade_outcomes.net_value`），
  导致两个后果：
    ① 无法回答「哪条车道真赚钱」——实测 S8 返佣策略 rebate 实收 $0 却没人发现；
    ② 无法判断「赚的是价差、资金费还是运气」。

六维定义（全部以 bp 计，正=对车道有利）：
  spread_bp    成交价相对当时中间价的捕获（maker 挂宽的核心收益）
  funding_bp   该持仓期内资金费收支
  price_bp     价格方向盈亏（做市库存的方向暴露）
  fee_bp       手续费（负值；Aster maker 0% → 0）
  slippage_bp  滑点（委托价 vs 成交价）
  points_usd   积分/返佣折算美元（单独维度，不折算成 bp）
  net_bp = spread + funding + price + fee + slippage
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ensured = False
_ensure_lock = threading.Lock()

DIMENSIONS = ("spread_bp", "funding_bp", "price_bp", "fee_bp", "slippage_bp")


def ensure_table() -> None:
    """建表（每进程一次）。"""
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS lane_ledger ("
                        " id BIGSERIAL PRIMARY KEY,"
                        " lane_id VARCHAR(64) NOT NULL,"
                        " symbol VARCHAR(32),"
                        " position_id VARCHAR(64),"
                        " event VARCHAR(24) NOT NULL DEFAULT 'fill',"
                        " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " notional DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " spread_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " funding_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " price_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " fee_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " slippage_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " points_usd DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " net_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " meta_json JSONB)"
                    ))
                    db.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_lane_ledger_lane_ts"
                        " ON lane_ledger (lane_id, ts DESC)"
                    ))
                    db.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_lane_ledger_ts"
                        " ON lane_ledger (ts DESC)"
                    ))
                    db.commit()
            _ensured = True
        except Exception as e:
            logger.warning("[LaneLedger] ensure_table 失败: %s", e)


def net_bp(
    spread_bp: float = 0.0,
    funding_bp: float = 0.0,
    price_bp: float = 0.0,
    fee_bp: float = 0.0,
    slippage_bp: float = 0.0,
) -> float:
    """六维中的五个 bp 维度求和（纯函数，便于单测）。"""
    return float(spread_bp) + float(funding_bp) + float(price_bp) + float(fee_bp) + float(slippage_bp)


def compute_fill_dimensions(
    *,
    side: str,
    fill_px: float,
    mid_px: float,
    fee_rate: float = 0.0,
    order_px: Optional[float] = None,
    funding_bp: float = 0.0,
    price_bp: float = 0.0,
) -> Dict[str, float]:
    """从一笔成交算出六维（纯函数）。

    - 买/多：捕获 = (mid − fill)/mid；卖/空：(fill − mid)/mid。maker 挂宽为正。
    - fee_bp = −fee_rate×1e4（0% maker → 0；4bp taker → −4bp）。
    - slippage_bp = 相对委托价的不利偏移（买单成交高于委托 → 负）。
    """
    side_l = str(side or "").lower()
    is_buy = side_l in ("buy", "long", "b")
    if not (fill_px and mid_px and mid_px > 0):
        return {k: 0.0 for k in DIMENSIONS}
    capture = ((mid_px - fill_px) if is_buy else (fill_px - mid_px)) / mid_px * 1e4
    slip = 0.0
    if order_px and order_px > 0:
        raw = ((order_px - fill_px) if is_buy else (fill_px - order_px)) / order_px * 1e4
        slip = raw if raw < 0 else 0.0     # 只有不利偏移计为滑点成本
    return {
        "spread_bp": round(capture, 4),
        "funding_bp": round(float(funding_bp), 4),
        "price_bp": round(float(price_bp), 4),
        "fee_bp": round(-float(fee_rate) * 1e4, 4),
        "slippage_bp": round(slip, 4),
    }


def record(
    *,
    lane_id: str,
    symbol: str = "",
    event: str = "fill",
    notional: float = 0.0,
    spread_bp: float = 0.0,
    funding_bp: float = 0.0,
    price_bp: float = 0.0,
    fee_bp: float = 0.0,
    slippage_bp: float = 0.0,
    points_usd: float = 0.0,
    position_id: Optional[str] = None,
    ts: Optional[datetime] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """写入一条账本记录。失败只记日志，绝不抛给交易链路。"""
    ensure_table()
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        n = net_bp(spread_bp, funding_bp, price_bp, fee_bp, slippage_bp)
        with system_identity():
            with SessionLocal() as db:
                db.execute(text(
                    "INSERT INTO lane_ledger (lane_id, symbol, position_id, event, ts,"
                    " notional, spread_bp, funding_bp, price_bp, fee_bp, slippage_bp,"
                    " points_usd, net_bp, meta_json) VALUES"
                    " (:lane, :sym, :pos, :ev, :ts, :notional, :sp, :fu, :pr, :fe, :sl,"
                    " :pts, :net, :meta)"
                ), {
                    "lane": lane_id, "sym": symbol or None, "pos": position_id,
                    "ev": event, "ts": ts or datetime.now(timezone.utc),
                    "notional": float(notional or 0.0),
                    "sp": float(spread_bp), "fu": float(funding_bp),
                    "pr": float(price_bp), "fe": float(fee_bp),
                    "sl": float(slippage_bp), "pts": float(points_usd),
                    "net": n,
                    "meta": json.dumps(meta, ensure_ascii=False, default=str) if meta else None,
                })
                db.commit()
        return True
    except Exception as e:
        logger.warning("[LaneLedger] record 失败: %s", e)
        return False


def record_fill(
    *,
    lane_id: str,
    symbol: str,
    side: str,
    qty: float,
    fill_px: float,
    mid_px: float,
    fee_rate: float = 0.0,
    order_px: Optional[float] = None,
    funding_bp: float = 0.0,
    price_bp: float = 0.0,
    points_usd: float = 0.0,
    position_id: Optional[str] = None,
    ts: Optional[datetime] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """便捷入口：从成交直接落六维账本。

    `meta` 会自动带上 side/qty/fill_px/mid_px，使 `open_positions()` 能仅凭账本
    重建持仓（账本 = 唯一事实源）。
    """
    dims = compute_fill_dimensions(
        side=side, fill_px=fill_px, mid_px=mid_px, fee_rate=fee_rate,
        order_px=order_px, funding_bp=funding_bp, price_bp=price_bp,
    )
    meta_full = {
        "side": str(side or "").lower(), "qty": abs(float(qty or 0.0)),
        "fill_px": float(fill_px or 0.0), "mid_px": float(mid_px or 0.0),
    }
    if meta:
        meta_full.update(meta)
    return record(
        lane_id=lane_id, symbol=symbol, event="fill",
        notional=abs(float(qty or 0.0) * float(fill_px or 0.0)),
        points_usd=points_usd, position_id=position_id, ts=ts, meta=meta_full,
        **dims,
    )


def attribution(days: float = 7.0, lane_id: Optional[str] = None,
                since: Optional[str] = None) -> Dict[str, Any]:
    """按车道聚合六维（bp 用名义加权，另给美元口径）。

    [2026-09-14 统计时代隔离] `since`（ISO 时间串）用于把统计口径钉在当前时代：
    配置/账户重构后，旧时代（其它账户、其它参数族）的账本行不应继续污染
    「今日/近 7 天/30 天」的展示口径。历史行仍保留在库中供审计。
    """
    ensure_table()
    where = "WHERE ts >= now() - make_interval(secs => :secs)"
    params: Dict[str, Any] = {"secs": float(days) * 86400.0}
    if lane_id:
        where += " AND lane_id = :lane"
        params["lane"] = lane_id
    if since:
        where += " AND ts >= CAST(:since AS timestamptz)"
        params["since"] = str(since)
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT lane_id, COUNT(*) AS n,"
                    " SUM(notional) AS notional,"
                    " SUM(spread_bp*notional)/NULLIF(SUM(notional),0) AS spread_bp,"
                    " SUM(funding_bp*notional)/NULLIF(SUM(notional),0) AS funding_bp,"
                    " SUM(price_bp*notional)/NULLIF(SUM(notional),0) AS price_bp,"
                    " SUM(fee_bp*notional)/NULLIF(SUM(notional),0) AS fee_bp,"
                    " SUM(slippage_bp*notional)/NULLIF(SUM(notional),0) AS slippage_bp,"
                    " SUM(net_bp*notional)/NULLIF(SUM(notional),0) AS net_bp,"
                    " SUM(net_bp*notional)/10000.0 AS net_usd,"
                    " SUM(points_usd) AS points_usd"
                    f" FROM lane_ledger {where} GROUP BY lane_id ORDER BY lane_id"
                ), params).mappings().all()
        by_lane: List[Dict[str, Any]] = []
        for r in rows:
            d = {k: (float(r[k]) if r[k] is not None else 0.0) for k in (
                "notional", "spread_bp", "funding_bp", "price_bp", "fee_bp",
                "slippage_bp", "net_bp", "net_usd", "points_usd")}
            d["lane_id"] = r["lane_id"]
            d["n"] = int(r["n"] or 0)
            by_lane.append(d)
        total = {k: round(sum(x[k] for x in by_lane), 4) for k in (
            "spread_bp", "funding_bp", "price_bp", "fee_bp", "slippage_bp",
            "net_bp", "net_usd", "points_usd")}
        total["n"] = sum(x["n"] for x in by_lane)
        total["notional"] = round(sum(x["notional"] for x in by_lane), 2)

        # 逐标的归因（前端「哪条车道在哪个币上赚钱」；影子期报告也要用）
        by_symbol: List[Dict[str, Any]] = []
        try:
            from sqlalchemy import text as _text

            from backend.core.tenant import system_identity as _si
            from backend.database.connection import SessionLocal as _SL

            with _si():
                with _SL() as db2:
                    srows = db2.execute(_text(
                        "SELECT lane_id, symbol, COUNT(*) AS n,"
                        " SUM(notional) AS notional,"
                        " SUM(spread_bp*notional)/NULLIF(SUM(notional),0) AS spread_bp,"
                        " SUM(price_bp*notional)/NULLIF(SUM(notional),0) AS price_bp,"
                        " SUM(fee_bp*notional)/NULLIF(SUM(notional),0) AS fee_bp,"
                        " SUM(net_bp*notional)/NULLIF(SUM(notional),0) AS net_bp,"
                        " SUM(net_bp*notional)/10000.0 AS net_usd"
                        f" FROM lane_ledger {where}"
                        " GROUP BY lane_id, symbol ORDER BY lane_id, symbol"
                    ), params).mappings().all()
            for r in srows:
                by_symbol.append({
                    "lane_id": r["lane_id"], "symbol": r["symbol"],
                    "n": int(r["n"] or 0),
                    "notional": round(float(r["notional"] or 0.0), 2),
                    **{k: round(float(r[k] or 0.0), 4) for k in (
                        "spread_bp", "price_bp", "fee_bp", "net_bp", "net_usd")},
                })
        except Exception as e:  # pragma: no cover - 聚合失败不影响主结果
            logger.debug("[LaneLedger] by_symbol 聚合失败: %s", e)

        return {"days": days, "lane_id": lane_id, "total": total,
                "by_lane": by_lane, "by_symbol": by_symbol}
    except Exception as e:
        logger.warning("[LaneLedger] attribution 失败: %s", e)
        return {"days": days, "lane_id": lane_id, "total": {}, "by_lane": []}


def daily_series(lane_id: Optional[str] = None, days: float = 7.0,
                 since: Optional[str] = None) -> List[Dict[str, Any]]:
    """按日聚合净收益（美元），供前端迷你曲线。

    [2026-09-14] `since`（ISO 串）= 统计时代起点，旧时代行不计入。
    """
    ensure_table()
    where = "WHERE ts >= now() - make_interval(secs => :secs)"
    params: Dict[str, Any] = {"secs": float(days) * 86400.0}
    if lane_id:
        where += " AND lane_id = :lane"
        params["lane"] = lane_id
    if since:
        where += " AND ts >= CAST(:since AS timestamptz)"
        params["since"] = str(since)
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD') AS d,"
                    " COUNT(*) AS n,"
                    " SUM(net_bp*notional)/10000.0 AS net_usd,"
                    " SUM(points_usd) AS points_usd"
                    f" FROM lane_ledger {where}"
                    " GROUP BY 1 ORDER BY 1"
                ), params).mappings().all()
        return [{
            "date": r["d"], "n": int(r["n"] or 0),
            "net_usd": round(float(r["net_usd"] or 0.0), 4),
            "points_usd": round(float(r["points_usd"] or 0.0), 4),
        } for r in rows]
    except Exception as e:
        logger.warning("[LaneLedger] daily_series 失败: %s", e)
        return []


# [F92 2026-09-14] 「已平」阈值：重建持仓是浮点累加，残差可达 1e-11 量级。
# 此前阈值 1e-15 ⇒ 尘埃仓位（对外四舍五入后 qty=0.0）仍保留 opened_ts，
# 前端就出现「空仓 + 持有 7m28s」这种自相矛盾的行（用户直接看得到）。
FLAT_EPS = 1e-10


def open_positions(
    *,
    lane_id: Optional[str] = None,
    days: float = 30.0,
    marks: Optional[Dict[str, float]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从账本成交重建各 (车道, 标的) 的当前持仓。

    账本是唯一事实源：不依赖各车道自己维护的持仓表，因此**新车道只要写账本
    就能出现在前端持仓页**。价格盈亏口径与 `market_maker.core` 一致
    （中间价对中间价），未平部分用 `marks` 算浮动盈亏。

    [2026-09-14 统计时代隔离] `since` = 统计时代起点：旧时代的成交行不再重建出
    仓位/归因（否则持仓页会残留旧配置的 6 币种行与旧已实现盈亏，实测 -$42.61）。

    [F92 2026-09-14] `until` = 上界（含）：对账时把账本裁到「运行态快照那一刻」，
    否则 tick 落在两次读取之间会产生**假分叉**（实测一次跳到 ±1 条腿 ≈ ±$300，
    下一轮又自愈）——巡检误报会直接毁掉可信度。
    """
    ensure_table()
    where = "WHERE event = 'fill' AND ts >= now() - make_interval(secs => :secs)"
    params: Dict[str, Any] = {"secs": float(days) * 86400.0}
    if lane_id:
        where += " AND lane_id = :lane"
        params["lane"] = lane_id
    if since:
        where += " AND ts >= CAST(:since AS timestamptz)"
        params["since"] = str(since)
    if until:
        where += " AND ts <= CAST(:until AS timestamptz)"
        params["until"] = str(until)
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT lane_id, symbol, ts, notional, spread_bp, funding_bp,"
                    " price_bp, fee_bp, slippage_bp, net_bp, points_usd, meta_json"
                    f" FROM lane_ledger {where} ORDER BY ts ASC"
                ), params).mappings().all()
    except Exception as e:
        logger.warning("[LaneLedger] open_positions 失败: %s", e)
        return []

    acc: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r["lane_id"], r["symbol"] or "")
        st = acc.setdefault(key, {
            "lane_id": r["lane_id"], "symbol": r["symbol"] or "",
            "qty": 0.0, "avg_px": 0.0, "avg_mid": 0.0, "opened_ts": None,
            "last_ts": None, "fills": 0, "net_bp": 0.0, "notional": 0.0,
            "spread_bp": 0.0, "price_bp": 0.0, "fee_bp": 0.0,
            "funding_bp": 0.0, "slippage_bp": 0.0, "points_usd": 0.0,
        })
        meta = r["meta_json"] or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        notional = float(r["notional"] or 0.0)
        qty = float(meta.get("qty") or 0.0)
        side = str(meta.get("side") or "").lower()
        if qty <= 0:
            # 老记录没有 qty → 用名义/成交价反推不可靠，跳过库存但保留归因
            qty = 0.0
        signed = qty if side in ("buy", "long", "b") else -qty
        fill_px = float(meta.get("fill_px") or 0.0)
        mid_px = float(meta.get("mid_px") or 0.0)
        st["fills"] += 1
        st["notional"] += notional
        for k in ("spread_bp", "price_bp", "fee_bp", "funding_bp", "slippage_bp"):
            st[k] += float(r[k] or 0.0) * notional
        st["net_bp"] += float(r["net_bp"] or 0.0) * notional
        st["points_usd"] += float(r["points_usd"] or 0.0)
        st["last_ts"] = r["ts"]

        if abs(signed) < FLAT_EPS:
            continue
        if abs(st["qty"]) < FLAT_EPS:
            st["qty"], st["avg_px"], st["avg_mid"] = signed, fill_px, mid_px
            st["opened_ts"] = r["ts"]
        elif st["qty"] * signed > 0:
            tot = abs(st["qty"]) + abs(signed)
            st["avg_px"] = (st["avg_px"] * abs(st["qty"]) + fill_px * abs(signed)) / tot
            st["avg_mid"] = (st["avg_mid"] * abs(st["qty"]) + mid_px * abs(signed)) / tot
            st["qty"] += signed
        else:
            st["qty"] += signed
            if abs(st["qty"]) < FLAT_EPS:
                st["qty"], st["avg_px"], st["avg_mid"], st["opened_ts"] = 0.0, 0.0, 0.0, None
            elif st["qty"] * signed > 0:
                st["avg_px"], st["avg_mid"], st["opened_ts"] = fill_px, mid_px, r["ts"]

    marks = marks or {}
    out: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for st in acc.values():
        n = st["notional"] or 0.0
        if st["fills"] == 0:
            continue
        w = (lambda k: round(st[k] / n, 4) if n > 0 else 0.0)
        mark = float(marks.get(st["symbol"]) or 0.0)
        unreal_usd = 0.0
        if abs(st["qty"]) > FLAT_EPS and mark > 0 and st["avg_mid"] > 0:
            unreal_usd = (mark - st["avg_mid"]) * st["qty"]
        hold_sec = 0.0
        if st["opened_ts"] is not None:
            hold_sec = max(0.0, (now - st["opened_ts"]).total_seconds())
        # side 必须与**对外返回的 qty** 同口径：qty 已四舍五入到 10 位，
        # 若按原始值判方向，会出现「qty=0 但 side=long」的自相矛盾。
        qty_out = round(st["qty"], 10)
        out.append({
            "lane_id": st["lane_id"], "symbol": st["symbol"],
            "qty": qty_out,
            "side": ("long" if qty_out > 0 else "short" if qty_out < 0 else "flat"),
            "notional_usd": round(abs(qty_out) * (mark or st["avg_px"]), 2),
            "avg_px": round(st["avg_px"], 10), "avg_mid": round(st["avg_mid"], 10),
            "mark_px": mark or None,
            "unrealized_usd": round(unreal_usd, 4),
            "opened_at": st["opened_ts"].isoformat() if st["opened_ts"] else None,
            "last_fill_at": st["last_ts"].isoformat() if st["last_ts"] else None,
            "hold_sec": round(hold_sec, 1),
            "fills": st["fills"], "notional": round(n, 2),
            "spread_bp": w("spread_bp"), "price_bp": w("price_bp"),
            "fee_bp": w("fee_bp"), "funding_bp": w("funding_bp"),
            "slippage_bp": w("slippage_bp"), "net_bp": w("net_bp"),
            "realized_usd": round(n * w("net_bp") / 1e4, 4),
            "points_usd": round(st["points_usd"], 4),
        })
    out.sort(key=lambda x: (x["lane_id"], -abs(x["qty"])))
    return out


# ═══════════════════════ 影子期晋升指标（真实数据可算） ═══════════════════════

def fill_rate_stats(lane_id: str, days: float = 7.0,
                    since: Optional[str] = None) -> Dict[str, Any]:
    """实测成交速率（笔/标的/小时）。

    为什么需要它：晋升判定里的 `fill_rate_ratio`（真实成交率 ≥ 离线模拟 30%）
    此前一直是 None——因为没人把「模拟盘实际成交」与「回放建模成交」对齐过。
    这里给出前一半（实测速率），调用方拿回放基线做比值即可。
    [2026-09-14] `since` = 统计时代起点（旧时代行不计入速率）。
    """
    ensure_table()
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        _w = ("WHERE lane_id = :l AND event = 'fill'"
              " AND ts >= now() - make_interval(secs => :s)")
        _p: Dict[str, Any] = {"l": lane_id, "s": float(days) * 86400.0}
        if since:
            _w += " AND ts >= CAST(:since AS timestamptz)"
            _p["since"] = str(since)
        with system_identity():
            with SessionLocal() as db:
                row = db.execute(text(
                    "SELECT COUNT(*) AS n, COUNT(DISTINCT symbol) AS syms,"
                    f" MIN(ts) AS mn, MAX(ts) AS mx FROM lane_ledger {_w}"
                ), _p).mappings().first()
        n = int(row["n"] or 0)
        syms = int(row["syms"] or 0)
        if n == 0 or not row["mn"] or not row["mx"] or syms == 0:
            return {"fills": n, "symbols": syms, "span_hours": 0.0,
                    "per_symbol_hour": None}
        span_h = (row["mx"] - row["mn"]).total_seconds() / 3600.0
        if span_h <= 0:
            return {"fills": n, "symbols": syms, "span_hours": 0.0,
                    "per_symbol_hour": None}
        return {
            "fills": n, "symbols": syms, "span_hours": round(span_h, 3),
            "first_ts": row["mn"].isoformat(), "last_ts": row["mx"].isoformat(),
            "per_symbol_hour": round(n / span_h / syms, 4),
        }
    except Exception as e:
        logger.warning("[LaneLedger] fill_rate_stats 失败: %s", e)
        return {"fills": 0, "symbols": 0, "span_hours": 0.0, "per_symbol_hour": None}


def max_drawdown_pct(lane_id: str, days: float = 30.0, equity: float = 0.0,
                     event: str = "fill", since: Optional[str] = None) -> Optional[float]:
    """已实现盈亏曲线的最大回撤（占权益百分比）。

    逐笔累计净收益（`net_bp × notional`），取峰值到谷底的最大跌幅。
    权益未知（<=0）时返回 None——不用未定义的分母造数。
    [2026-09-14] `since` = 统计时代起点（旧时代行不计入回撤）。
    """
    if equity <= 0:
        return None
    ensure_table()
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        _w = ("WHERE lane_id = :l AND event = :e"
              " AND ts >= now() - make_interval(secs => :s)")
        if since:
            _w += " AND ts >= CAST(:since AS timestamptz)"
        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT ts, net_bp, notional FROM lane_ledger"
                    f" {_w} ORDER BY ts"
                ), {"l": lane_id, "e": event,
                    "s": float(days) * 86400.0,
                    **({"since": str(since)} if since else {})}).mappings().all()
        if not rows:
            return None
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in rows:
            cum += float(r["net_bp"] or 0.0) * float(r["notional"] or 0.0) / 1e4
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        return round(max_dd / float(equity) * 100.0, 4)
    except Exception as e:
        logger.warning("[LaneLedger] max_drawdown_pct 失败: %s", e)
        return None


def fill_rate_ratio(lane_id: str, *, baseline_per_symbol_hour: float,
                    days: float = 7.0, since: Optional[str] = None) -> Optional[float]:
    """实测成交速率 ÷ 回放建模速率（晋升判定的 `fill_rate_ratio`）。

    基线缺失或为 0 时返回 None（fail-closed，不拿未知分母凑数）。
    """
    if not baseline_per_symbol_hour or baseline_per_symbol_hour <= 0:
        return None
    st = fill_rate_stats(lane_id, days=days, since=since)
    live = st.get("per_symbol_hour")
    if not live:
        return None
    return round(float(live) / float(baseline_per_symbol_hour), 4)


def flatten_stats(lane_id: str, days: float = 7.0,
                  since: Optional[str] = None) -> Dict[str, Any]:
    """平仓诊断：占比与**价格维度**成本（bp）。

    为什么单独看它：实测挂单成交 +1.77bp（与模型一致），但超时平仓 −26.7bp、
    占 28.8%——净期望为负全在这里。回放窗口同口径只有 14.0% / −3.4bp，
    两个数字放在一起就能判断「是模型错还是市场变差」。
    [2026-09-14] `since` = 统计时代起点（旧时代行不计入平仓诊断）。
    """
    ensure_table()
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        _w = ("WHERE lane_id = :l AND event = 'fill'"
              " AND ts >= now() - make_interval(secs => :s)")
        _p: Dict[str, Any] = {"l": lane_id, "s": float(days) * 86400.0}
        if since:
            _w += " AND ts >= CAST(:since AS timestamptz)"
            _p["since"] = str(since)
        with system_identity():
            with SessionLocal() as db:
                row = db.execute(text(
                    "SELECT COUNT(*) AS n,"
                    " COUNT(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')) AS fl,"
                    " SUM(notional) FILTER (WHERE meta_json->>'flatten' IN ('true','True'))"
                    "   AS fl_notional,"
                    " SUM(price_bp * notional) FILTER"
                    "   (WHERE meta_json->>'flatten' IN ('true','True')) AS fl_price,"
                    " AVG(net_bp) FILTER (WHERE meta_json->>'flatten' IN ('true','True'))"
                    "   AS fl_net_bp"
                    f" FROM lane_ledger {_w}"
                ), _p).mappings().first()
        n = int(row["n"] or 0)
        fl = int(row["fl"] or 0)
        fl_notional = float(row["fl_notional"] or 0.0)
        return {
            "fills": n, "flattens": fl,
            "flatten_share": round(fl / n, 4) if n else None,
            "flatten_price_bp": (round(float(row["fl_price"] or 0.0) / fl_notional, 4)
                                 if fl_notional > 0 else None),
            "flatten_net_bp": (round(float(row["fl_net_bp"]), 4)
                               if fl and row["fl_net_bp"] is not None else None),
        }
    except Exception as e:
        logger.warning("[LaneLedger] flatten_stats 失败: %s", e)
        return {"fills": 0, "flattens": 0, "flatten_share": None,
                "flatten_price_bp": None, "flatten_net_bp": None}
