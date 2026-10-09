"""
Paper 账户权益曲线

仪表盘「当前」数字来自 paper_balances.total_equity，
但旧的 account_asset_snapshots /asset-curve/timeframe 走的是 Arena/AI 账户模型，
paper 几乎不写快照，导致图表停在初始资金或空白。

本模块：
1. 优先用已有 AccountAssetSnapshot（若足够）
2. 否则用 paper_orders 累计重建「已实现权益路径」
3. 末点始终对齐当前 paper 总权益（含浮动）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PERIOD_DAYS = {"7d": 7, "30d": 30, "all": None}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ts(dt: datetime) -> int:
    return int(_ensure_utc(dt).timestamp())


def build_paper_equity_curve(
    db: Session,
    account_id: int,
    period: str = "7d",
    *,
    max_points: int = 400,
) -> Dict[str, Any]:
    # [2026-09-23 修复] 旧实现的两个口径问题（历史段=订单账本累加、末点强锚 total_equity）
    # 已由 `build_paper_equity_series` 取代（持仓账本事件溯源 + 订单账本费用 + 重置边界 + 保极值降采样）。
    # 本函数**保留签名与返回形状**（`points:[{time,value}]`），内部改走权威口径 ⇒ 仪表盘等既有消费者
    # 无需改前端即可拿到正确数据；任一步失败则回退下方的旧实现（fail-open，保持可用）。
    try:
        s = build_paper_equity_series(db, account_id, period, max_points=max_points)
        if s.get("points"):
            account_name = None
            try:
                from backend.database.models import Account as _Acct
                _a = db.query(_Acct).filter(_Acct.id == int(account_id)).first()
                account_name = getattr(_a, "name", None)
            except Exception:
                pass
            return {
                "account_id": int(account_id),
                "account_name": account_name,
                "period": s["period"],
                "source": "closed_positions",
                "initial_balance": s["initial_balance"],
                "current_equity": round(s["realized_end"] + s["floating_now"], 4),
                "points": [{"time": p["t"], "value": p["v"]} for p in s["points"]],
                # 附加字段（既有消费者忽略即可）
                "floating_now": s["floating_now"],
                "balance_total_equity": s["balance_total_equity"],
                "peak_equity": s["peak_equity"],
                "max_drawdown_usd": s["max_drawdown_usd"],
                "max_drawdown_pct": s["max_drawdown_pct"],
                "costs_in_window": s["costs_in_window"],
                "reconcile": s["reconcile"],
                "baseline": s["baseline"],
            }
    except Exception as _delegate_err:  # noqa: BLE001
        logger.warning("[PaperEquityCurve] 权威口径失败，回退旧实现: %s", _delegate_err)

    from backend.database.models import (
        Account,
        AccountAssetSnapshot,
        PaperBalance,
        PaperOrder,
    )

    period_key = (period or "7d").lower().strip()
    if period_key not in _PERIOD_DAYS:
        period_key = "7d"

    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if not account:
        return {"account_id": account_id, "period": period_key, "points": [], "source": "none"}

    bal = db.query(PaperBalance).filter(PaperBalance.account_id == int(account_id)).first()
    initial = float(bal.initial_balance) if bal else float(account.initial_capital or 0)
    now = datetime.now(timezone.utc)

    # 当前权益（含浮动）
    current_equity = initial
    try:
        from backend.services.paper_trading_engine import paper_engine

        live = paper_engine.get_balance(db, int(account_id)) or {}
        if live.get("total_equity") is not None:
            current_equity = float(live["total_equity"])
        elif bal:
            current_equity = float(bal.total_equity or initial)
    except Exception as e:
        logger.debug("[PaperEquityCurve] live balance: %s", e)
        if bal:
            current_equity = float(bal.total_equity or initial)

    cutoff: Optional[datetime] = None
    days = _PERIOD_DAYS[period_key]
    if days is not None:
        cutoff = now - timedelta(days=days)

    points: List[Dict[str, Any]] = []
    source = "orders"

    # 1) 快照路径（若 paper 已开始写入）
    snap_q = db.query(AccountAssetSnapshot).filter(
        AccountAssetSnapshot.account_id == int(account_id)
    )
    if cutoff is not None:
        snap_q = snap_q.filter(AccountAssetSnapshot.event_time >= cutoff.replace(tzinfo=None))
    snaps = snap_q.order_by(AccountAssetSnapshot.event_time.asc()).all()
    if len(snaps) >= 2:
        source = "snapshots"
        for s in snaps:
            et = s.event_time
            if et is None:
                continue
            points.append(
                {
                    "time": _ts(et),
                    "value": round(float(s.total_assets or 0), 4),
                }
            )
        # [2026-08-26 修复] 快照可信度校验 + 末点锚定 live 权益（文档承诺但从未实现）：
        # 快照(total_assets)与 current(total_equity) 量级偏差>2x → 快照不可信（旧 Arena
        # 口径/单位漂移，曾致曲线显示 ~780 而 KPI 109），整体回退订单重建。
        if points and current_equity > 0:
            _last_snap = float(points[-1]["value"] or 0)
            if abs(_last_snap - current_equity) > 2.0 * current_equity:
                logger.warning(
                    "[PaperEquityCurve] 快照不可信: last=%.2f vs current=%.2f → 回退订单重建",
                    _last_snap, current_equity,
                )
                points, source = [], "orders"
            else:
                if int(now.timestamp()) - points[-1]["time"] > 300:
                    points.append({"time": int(now.timestamp()), "value": round(current_equity, 4)})
                else:
                    points[-1]["value"] = round(current_equity, 4)
    if not points:
        # 2) 订单重建：equity ≈ initial + Σpnl − Σfee（末点再叠当前浮动）
        source = "orders"
        oq = (
            db.query(PaperOrder)
            .filter(
                PaperOrder.account_id == int(account_id),
                PaperOrder.status == "filled",
                PaperOrder.filled_at.isnot(None),
            )
            .order_by(PaperOrder.filled_at.asc())
        )
        orders = oq.all()

        start_dt = None
        if bal and bal.last_reset_at:
            start_dt = bal.last_reset_at
        elif bal and bal.created_at:
            start_dt = bal.created_at
        elif orders:
            start_dt = orders[0].filled_at
        else:
            start_dt = now

        start_dt = _ensure_utc(start_dt)
        if cutoff is not None and start_dt < cutoff:
            # 时段起点：先滚到 cutoff 前的累计权益
            equity = initial
            for o in orders:
                ft = o.filled_at
                if ft is None:
                    continue
                ft_u = _ensure_utc(ft)
                if ft_u >= cutoff:
                    break
                equity += float(o.pnl or 0) - float(o.fee or 0)
            points.append({"time": _ts(cutoff), "value": round(equity, 4)})
            running = equity
            for o in orders:
                ft = o.filled_at
                if ft is None:
                    continue
                ft_u = _ensure_utc(ft)
                if ft_u < cutoff:
                    continue
                running += float(o.pnl or 0) - float(o.fee or 0)
                points.append({"time": _ts(ft_u), "value": round(running, 4)})
        else:
            points.append({"time": _ts(start_dt), "value": round(initial, 4)})
            running = initial
            for o in orders:
                ft = o.filled_at
                if ft is None:
                    continue
                ft_u = _ensure_utc(ft)
                if cutoff is not None and ft_u < cutoff:
                    continue
                running += float(o.pnl or 0) - float(o.fee or 0)
                points.append({"time": _ts(ft_u), "value": round(running, 4)})

    # 末点对齐当前权益
    now_ts = _ts(now)
    if points and points[-1]["time"] == now_ts:
        points[-1]["value"] = round(current_equity, 4)
    else:
        points.append({"time": now_ts, "value": round(current_equity, 4)})

    # 去重同秒（保留最后一个）
    dedup: Dict[int, float] = {}
    for p in points:
        dedup[int(p["time"])] = float(p["value"])
    cleaned = [{"time": t, "value": v} for t, v in sorted(dedup.items())]

    # 降采样
    if len(cleaned) > max_points:
        step = max(1, len(cleaned) // max_points)
        sampled = cleaned[::step]
        if sampled[-1]["time"] != cleaned[-1]["time"]:
            sampled.append(cleaned[-1])
        cleaned = sampled

    # 至少 2 点才能画线：若只有末点，补一个起点
    if len(cleaned) == 1:
        t1 = cleaned[0]["time"]
        cleaned = [
            {"time": max(0, t1 - 3600), "value": round(initial, 4)},
            cleaned[0],
        ]

    # 强制末点 = 当前权益（与仪表盘「当前」一致，含浮动）
    now_ts2 = _ts(datetime.now(timezone.utc))
    if cleaned[-1]["time"] >= now_ts2 - 2:
        cleaned[-1] = {"time": cleaned[-1]["time"], "value": round(current_equity, 4)}
    else:
        cleaned.append({"time": now_ts2, "value": round(current_equity, 4)})

    return {
        "account_id": int(account_id),
        "account_name": account.name,
        "period": period_key,
        "source": source,
        "initial_balance": round(initial, 4),
        "current_equity": round(current_equity, 4),
        "points": cleaned,
    }


def build_paper_equity_series(
    db: Session,
    account_id: int,
    period: str = "30d",
    *,
    max_points: int = 400,
) -> Dict[str, Any]:
    """[2026-09-23 新方法] Paper 账户**已实现权益序列**（事件溯源，权威口径）。

    与旧 `build_paper_equity_curve`（主页在用）的区别 —— 旧实现的四个问题：
      1. 用 `paper_orders` 逐单 pnl−fee 累加：订单表**没有 position_id**，且历史 fee 漏记（实测订单级 $39.44
         vs 持仓级 $6.41 两套账），口径不是权威；
      2. 只算已实现，却把**末点强制改写为含浮动的 total_equity** ⇒ 历史段与末点两种口径混在一条线上，
         末段会出现"凭空台阶"；
      3. 完全**不含资金费**（现已回填 `funding_paid/received`）；
      4. 降采样用 `[::step]` 等步长抽点 ⇒ 会吃掉峰值/谷值，回撤读数不可信；且每请求拉全表订单。

    本实现（口径写死，可复核；2026-09-23 回滚后默认订单账本）：
      realized(t) = initial_balance + 事件累计 [ 订单腿 pnl（默认，PAPER_BALANCE_PNL_SOURCE=orders）
                                                       + 资金费账本 payment ]
                                                       - 订单账本手续费（开仓+平仓，窗口内末值一次性扣）
      曾短暂用持仓账本（positions 口径）：多笔仓 closed.unrealized_pnl 只记最后一腿 => 权益虚高
        ~+$125（用户否决），已回滚；positions 口径保留在开关后作对照。
      时间：paper_orders.created_at / paper_funding_ledger.settled_at 为北京钟面 naive，按 CST->epoch 换算；
    """
    from sqlalchemy import text as _t

    period_key = (period or "30d").lower().strip()
    days_map = {"7d": 7, "30d": 30, "90d": 90, "all": None}
    if period_key not in days_map:
        period_key = "30d"

    acct = int(account_id)
    db.execute(_t("SET app.is_admin='on'"))
    row = db.execute(_t(
        """SELECT b.initial_balance, b.total_equity, b.last_reset_at,
                  (SELECT count(*) FROM paper_positions p WHERE p.account_id=:a AND p.status='open') open_n
           FROM paper_balances b WHERE b.account_id=:a"""), {"a": acct}).mappings().first()
    initial = float((row or {}).get("initial_balance") or 0.0)
    balance_equity = float((row or {}).get("total_equity") or 0.0)
    open_n = int((row or {}).get("open_n") or 0)
    reset_at = (row or {}).get("last_reset_at")
    # [2026-09-23 修正] 账户可能被软重置过（`last_reset_at`）：余额账本只统计重置后的订单，
    # 本端点原先统计**全历史** 3183 笔已平仓 ⇒ 与余额口径差 $109.58。现遵守同一重置边界，
    # 并把**开仓费**（订单账本 `close_reason IS NULL` 的 filled 单费用，实测重置后 $51.05）
    # 一次性计入起点——持仓行不记开仓费，漏扣会高估权益。
    reset_naive = reset_at.replace(tzinfo=None) if getattr(reset_at, "tzinfo", None) else reset_at
    pos_since = "AND closed_at >= :r" if reset_naive is not None else ""
    ord_since = "AND created_at >= :r" if reset_naive is not None else ""
    fund_since = "AND settled_at >= :r" if reset_naive is not None else ""

    now = datetime.now(timezone.utc)
    days = days_map[period_key]
    cutoff = now - timedelta(days=days) if days else None
    cutoff_naive = cutoff.replace(tzinfo=None) + timedelta(hours=8) if cutoff else None  # CST 钟面

    def _agg(where_extra: str, params: Dict[str, Any]) -> Dict[str, float]:
        q = db.execute(_t(
            f"""SELECT COALESCE(SUM(unrealized_pnl),0) pnl,
                       COALESCE(SUM(partial_fee_paid + COALESCE(final_fee_paid,0)),0) fee,
                       COALESCE(SUM(funding_paid - funding_received),0) fund,
                       count(*) n
                FROM paper_positions
                WHERE account_id=:a AND status IN ('closed','liquidated') {pos_since} {where_extra}"""),
            params).mappings().first()
        return {"pnl": float(q["pnl"]), "fee": float(q["fee"]), "fund": float(q["fund"]), "n": int(q["n"])}

    def _order_fees_all(where_extra: str, params: Dict[str, Any]) -> Dict[str, float]:
        """订单账本手续费（开仓+平仓，与 `_recalc_balance` 同口径），并给出开/平拆分。"""
        q = db.execute(_t(
            f"""SELECT COALESCE(SUM(fee),0) all_fee,
                       COALESCE(SUM(CASE WHEN close_reason IS NULL THEN fee ELSE 0 END),0) entry_fee,
                       COALESCE(SUM(CASE WHEN close_reason IS NOT NULL THEN fee ELSE 0 END),0) exit_fee
                FROM paper_orders
                WHERE account_id=:a AND status='filled' {ord_since} {where_extra}"""), params).mappings().first()
        return {"all": float(q["all_fee"]), "entry": float(q["entry_fee"]), "exit": float(q["exit_fee"])}

    _p0: Dict[str, Any] = {"a": acct, **({"r": reset_naive} if reset_naive is not None else {})}
    base = {"pnl": 0.0, "fee": 0.0, "fund": 0.0, "n": 0}
    fees_before = {"all": 0.0, "entry": 0.0, "exit": 0.0}
    fees_window = {"all": 0.0, "entry": 0.0, "exit": 0.0}
    if cutoff_naive is not None:
        fees_before = _order_fees_all("AND created_at < :c", {**_p0, "c": cutoff_naive})
        fees_window = _order_fees_all("AND created_at >= :c", {**_p0, "c": cutoff_naive})
    else:
        fees_before = _order_fees_all("", _p0)
    # [2026-09-23 口径对齐] 费用统一用**订单账本**（开仓+平仓，与 `_recalc_balance` 完全同口径）：
    # 持仓行只记平仓费、不记开仓费；用订单账本可避免"我这边少扣开仓费"的口径差。
    # 窗口内费用在末值一次性扣除（口径已注明）。

    # [2026-09-23 回滚] 已实现盈亏口径开关，与 `_recalc_balance` 同源（PAPER_BALANCE_PNL_SOURCE）：
    #   · 默认 "orders"（订单账本逐腿）：持仓账本 closed.unrealized_pnl 多笔仓只记最后一腿，
    #     曾使权益虚高 ≈ +$125（用户否决），故回滚；
    #   · "positions"（持仓账本，对照实验用，勿默认启用）。
    from backend.config.settings import PAPER_BALANCE_PNL_SOURCE as _pnl_src
    cst = timezone(timedelta(hours=8))
    pts: List[Dict[str, Any]] = []
    if _pnl_src == "positions":
        if cutoff_naive is not None:
            base = _agg("AND closed_at < :c", {**_p0, "c": cutoff_naive})
        start_equity = initial + base["pnl"] - base["fund"] - fees_before["all"]
        rows = db.execute(_t(
            """SELECT closed_at AS t, unrealized_pnl AS pnl,
                      partial_fee_paid + COALESCE(final_fee_paid,0) AS fee,
                      funding_paid - funding_received AS fund
               FROM paper_positions
               WHERE account_id=:a AND status IN ('closed','liquidated')
                 AND closed_at IS NOT NULL """ + pos_since + " " +
            ("AND closed_at >= :c " if cutoff_naive else "") +
            "ORDER BY closed_at"), {**_p0, **({"c": cutoff_naive} if cutoff_naive else {})}).mappings().all()
        if cutoff is not None:
            pts.append({"t": int(cutoff.timestamp()), "v": round(start_equity, 4)})
        run = start_equity
        win_fund = 0.0
        for r in rows:
            run += float(r["pnl"] or 0) - float(r["fund"] or 0)
            win_fund += float(r["fund"] or 0)
            t = r["t"]
            ts = int((t.replace(tzinfo=cst) if t.tzinfo is None else t).timestamp())
            pts.append({"t": ts, "v": round(run, 4)})
        realized_end = (run - fees_window["all"]) if (rows or cutoff) else initial
        base_n = int(base["n"] or 0)
        closed_n = len(rows)
    else:
        # 订单口径：事件 = 带 pnl 的订单腿（created_at，北京钟面）+ 资金费账本（settled_at）。
        def _ord_agg(where_extra: str, params: Dict[str, Any]) -> Dict[str, float]:
            q = db.execute(_t(
                f"""SELECT COALESCE(SUM(pnl),0) pnl, count(*) n
                     FROM paper_orders
                     WHERE account_id=:a AND pnl IS NOT NULL {ord_since} {where_extra}"""),
                params).mappings().first()
            return {"pnl": float(q["pnl"]), "n": int(q["n"])}

        def _fund_agg(where_extra: str, params: Dict[str, Any]) -> Dict[str, float]:
            q = db.execute(_t(
                f"""SELECT COALESCE(SUM(payment),0) fund, count(*) n
                     FROM paper_funding_ledger
                     WHERE account_id=:a {fund_since} {where_extra}"""),
                params).mappings().first()
            return {"fund": float(q["fund"]), "n": int(q["n"])}

        base_o = {"pnl": 0.0, "n": 0}
        base_f = {"fund": 0.0, "n": 0}
        if cutoff_naive is not None:
            base_o = _ord_agg("AND created_at < :c", {**_p0, "c": cutoff_naive})
            base_f = _fund_agg("AND settled_at < :c", {**_p0, "c": cutoff_naive})
        start_equity = initial + base_o["pnl"] + base_f["fund"] - fees_before["all"]

        evts: List[tuple] = []  # (naive_t, pnl_delta, fund_delta)
        for r in db.execute(_t(
            f"""SELECT created_at AS t, pnl FROM paper_orders
                 WHERE account_id=:a AND pnl IS NOT NULL {ord_since}
                 {("AND created_at >= :c" if cutoff_naive else "")} ORDER BY created_at"""),
            {**_p0, **({"c": cutoff_naive} if cutoff_naive else {})}).mappings().all():
            evts.append((r["t"], float(r["pnl"] or 0), 0.0))
        for r in db.execute(_t(
            f"""SELECT settled_at AS t, payment FROM paper_funding_ledger
                 WHERE account_id=:a {fund_since}
                 {("AND settled_at >= :c" if cutoff_naive else "")} ORDER BY settled_at"""),
            {**_p0, **({"c": cutoff_naive} if cutoff_naive else {})}).mappings().all():
            evts.append((r["t"], 0.0, float(r["payment"] or 0)))
        evts.sort(key=lambda e: e[0])

        if cutoff is not None:
            pts.append({"t": int(cutoff.timestamp()), "v": round(start_equity, 4)})
        run = start_equity
        win_fund = 0.0
        for t, dp, df_ in evts:
            run += dp + df_
            win_fund += df_
            ts = int((t.replace(tzinfo=cst) if t.tzinfo is None else t).timestamp())
            pts.append({"t": ts, "v": round(run, 4)})
        realized_end = (run - fees_window["all"]) if (evts or cutoff) else initial
        base_n = int(base_o["n"] or 0)
        closed_n = len(evts)

    floating = float(db.execute(_t(
        """SELECT COALESCE(SUM(unrealized_pnl),0) FROM paper_positions
           WHERE account_id=:a AND status='open'"""), {"a": acct}).scalar() or 0.0)

    # 峰值/最大回撤：在**未降采样**的完整序列上算（旧实现抽点后才画，会吃掉极值）
    peak, mdd, mdd_pct, peak_ts = start_equity, 0.0, 0.0, (pts[0]["t"] if pts else None)
    for p in pts:
        if p["v"] > peak:
            peak, peak_ts = p["v"], p["t"]
        dd = peak - p["v"]
        if dd > mdd:
            mdd = dd
            mdd_pct = (dd / peak * 100.0) if peak > 0 else 0.0

    # 保极值降采样：时间桶内取 min/max/last（旧实现 [::step] 会丢峰谷）
    if len(pts) > max_points:
        t0, t1 = pts[0]["t"], pts[-1]["t"]
        span = max(1, t1 - t0)
        bucket = max(1, span // max_points)
        bucketed: Dict[int, List[Dict[str, Any]]] = {}
        for p in pts:
            bucketed.setdefault((p["t"] - t0) // bucket, []).append(p)
        keep: List[Dict[str, Any]] = []
        for _k, group in sorted(bucketed.items()):
            vals = [g["v"] for g in group]
            lo = min(group, key=lambda g: g["v"])
            hi = max(group, key=lambda g: g["v"])
            for cand in (lo, hi, group[-1] if vals else None):
                if cand and cand not in keep:
                    keep.append(cand)
        keep.sort(key=lambda p: p["t"])
        pts = keep

    reconcile = {
        "realized_end": round(realized_end, 4),
        "floating_now": round(floating, 4),
        "realized_plus_floating": round(realized_end + floating, 4),
        "balance_total_equity": round(balance_equity, 4),
        "diff": round(realized_end + floating - balance_equity, 4),
    }
    reconcile["ok"] = abs(reconcile["diff"]) <= max(1.0, abs(balance_equity) * 0.01)

    return {
        "account_id": acct,
        "period": period_key,
        "source": "closed_positions",
        "timezone": "Asia/Shanghai(closed_at 钟面)",
        "initial_balance": round(initial, 4),
        "start_equity": round(start_equity, 4),
        "realized_end": round(realized_end, 4),
        "floating_now": round(floating, 4),
        "equity_total_now": round(realized_end + floating, 4),
        "balance_total_equity": round(balance_equity, 4),
        "peak_equity": round(peak, 4),
        "peak_at": peak_ts,
        "max_drawdown_usd": round(mdd, 4),
        "max_drawdown_pct": round(mdd_pct, 4),
        "counts": {"closed_in_window": closed_n, "closed_before_window": base_n, "open_now": open_n},
        "costs_in_window": {
            "fees": round(fees_window["all"], 4),
            "entry_fees": round(fees_window["entry"], 4),
            "exit_fees": round(fees_window["exit"], 4),
            "funding_net": round(win_fund, 4),
        },
        "baseline": {"last_reset_at": reset_at.isoformat() if reset_at else None,
                     "note": "遵守 paper_balances.last_reset_at 软重置边界；开仓费一次性计入起点/末值"},
        "points": pts,
        "reconcile": reconcile,
    }


def record_paper_equity_snapshot(db: Session, account_id: int) -> None:
    """把当前 paper 权益写入 account_asset_snapshots（供后续走快照路径）。"""
    from backend.database.models import AccountAssetSnapshot
    from backend.services.asset_curve_calculator import invalidate_asset_curve_cache
    from backend.services.paper_trading_engine import paper_engine

    live = paper_engine.get_balance(db, int(account_id)) or {}
    equity = float(live.get("total_equity") or 0)
    cash = float(live.get("available_balance") or 0)
    frozen = float(live.get("frozen_margin") or 0)
    upnl = float(live.get("unrealized_pnl") or 0)
    if equity <= 0:
        return
    db.add(
        AccountAssetSnapshot(
            account_id=int(account_id),
            total_assets=equity,
            cash=cash,
            positions_value=frozen + upnl,
            trigger_symbol="paper",
            trigger_market="PAPER",
            event_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    try:
        db.commit()
        invalidate_asset_curve_cache()
    except Exception:
        db.rollback()
        raise
