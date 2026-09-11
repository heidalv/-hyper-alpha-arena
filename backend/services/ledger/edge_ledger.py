# -*- coding: utf-8 -*-
"""边际账本 edge_ledger（v3 F2）。

目的
----
回答一个此前全系统没有任何地方能回答的问题："哪条车道在扣完手续费之后真的赚钱？"
所有资本分配（E4 三桶）、晋升门（影子→小资金→灰度）、看板 KPI 都只允许从这里读数，
不再各自用不同口径拼数字（旧口径：session_stats 毛盈亏、tier_circuit 只算毛、
learning 层把费拖型小赢记成 win）。

口径（写死，改口径必须改这里并写注释）
------------------------------------
- 样本：paper_positions status ∈ {closed, liquidated}，closed_at 在窗口内。
- 毛盈亏 gross = unrealized_pnl + partial_realized_pnl（引擎在全平时把最终盈亏写回 unrealized_pnl）。
- 手续费 fees = 开仓费（估：开仓名义 × taker）+ 平仓费/部分平仓费（trade_facts.fees 真值；
  缺失时估：平仓名义 × taker + partial_fee_paid）。开仓费引擎未落到持仓行，只能估；
  账户级真值另由 paper_orders.fee 汇总给出，两者差额作为对账误差展示。
- 净 net = gross − fees；net_bp = net / 开仓名义 × 1e4。
- PF = Σ正净 / |Σ负净|；CI 为 net_bp 均值的 95% 正态区间（n<8 不给区间）。
- 车道 lane = tier（short/mid/long）× side（long/short）；再按 nature、出场大类、币种细分。

另附：
- 账户真值（paper_balances：权益、initial、total_fee_paid）与窗口内 paper_orders 费用合计；
- trade_facts 覆盖率（学习样本完整性）；
- 短线影子车道（scalp_signal_log action='shadow_fill'）triple-barrier 结算统计与
  晋升门状态（N ≥ 300 且 95% 下界 > 0）。
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_Z95 = 1.959964


def _taker_rate() -> float:
    try:
        from backend.services.backtest_engine.backtest_engine import TAKER_FEE
        return float(TAKER_FEE)
    except Exception:
        return 0.00035


def _classify_close_reason(reason: str) -> str:
    """出场大类（与 _ptmp 诊断脚本口径一致，便于前后对照）。"""
    r = (reason or "").lower()
    if not r or r == "?":
        return "Z_other"
    if "take_profit" in r or r == "tp" or r.startswith("breakeven_tp") or "profit_lock" in r or r == "safety_tp":
        return "A_tp"
    if r == "sl" or "stop_loss" in r or r.startswith("breakeven_sl"):
        return "B_sl"
    if "timeout" in r or "decay" in r or "fast_cut" in r or "max_hold" in r or "no_progress" in r:
        return "C_time"
    if "symbol_removed" in r:
        return "D_rotation"
    if "trail" in r:
        return "E_trailing"
    if "profit_drawdown" in r:
        return "F_profit_dd"
    if "trend_broken" in r or "trend_weaken" in r:
        return "G_trend_broken"
    if "midlong" in r:
        return "H_midlong_gov"
    if "master" in r:
        return "I_master_close"
    if "dust" in r:
        return "J_dust"
    if "liquidat" in r:
        return "K_liquidation"
    return "Z_other"


def _stats(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    n = len(rows)
    if n == 0:
        return {"n": 0}
    nets = [float(r["net"]) for r in rows]
    bps = [float(r["net_bp"]) for r in rows if r.get("net_bp") is not None]
    gross = sum(float(r["gross"]) for r in rows)
    fees = sum(float(r["fees"]) for r in rows)
    net = sum(nets)
    pos = sum(x for x in nets if x > 0)
    neg = -sum(x for x in nets if x < 0)
    wins = sum(1 for x in nets if x > 0)
    holds = [float(r["hold_min"]) for r in rows if r.get("hold_min") is not None]
    out: Dict[str, Any] = {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 4),
        "gross": round(gross, 4),
        "fees": round(fees, 4),
        "net": round(net, 4),
        "fee_share_of_gross_abs": round(fees / abs(gross), 4) if abs(gross) > 1e-9 else None,
        "pf": round(pos / neg, 3) if neg > 1e-9 else (None if pos <= 0 else float("inf")),
        "avg_net": round(net / n, 4),
        "avg_hold_min": round(sum(holds) / len(holds), 1) if holds else None,
        "notional": round(sum(float(r["notional"]) for r in rows), 2),
    }
    if bps:
        m = sum(bps) / len(bps)
        out["avg_net_bp"] = round(m, 2)
        if len(bps) >= 8:
            var = sum((x - m) ** 2 for x in bps) / (len(bps) - 1)
            se = math.sqrt(var / len(bps))
            out["ci95_bp"] = [round(m - _Z95 * se, 2), round(m + _Z95 * se, 2)]
            out["edge_verdict"] = (
                "positive" if m - _Z95 * se > 0 else ("negative" if m + _Z95 * se < 0 else "inconclusive")
            )
        else:
            out["ci95_bp"] = None
            out["edge_verdict"] = "insufficient_n"
    if out.get("pf") == float("inf"):
        out["pf"] = None
    return out


def _group(rows: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(str(key_fn(r)), []).append(r)
    return {k: _stats(v) for k, v in sorted(buckets.items(), key=lambda kv: kv[0])}


def load_trade_rows(db, account_id: int, days: int) -> List[Dict[str, Any]]:
    """窗口内的逐笔（逐持仓）净额行；见模块 docstring 的口径说明。"""
    from sqlalchemy import text as _t
    since = datetime.now() - timedelta(days=int(days))
    rate = _taker_rate()
    sql = _t(
        """
        SELECT p.id, p.symbol, p.side, COALESCE(p.timeframe_tier, '?') AS tier,
               COALESCE(p.trade_nature, '?') AS nature, COALESCE(p.strategy_id, '') AS strategy_id,
               COALESCE(p.close_reason, '') AS close_reason, p.opened_at, p.closed_at,
               COALESCE(p.leverage, 1) AS leverage, COALESCE(p.original_size, p.size, 0) AS open_size,
               COALESCE(p.size, 0) AS last_size, COALESCE(p.entry_price, 0) AS entry_price,
               COALESCE(p.close_price, 0) AS close_price,
               COALESCE(p.unrealized_pnl, 0) + COALESCE(p.partial_realized_pnl, 0) AS gross,
               COALESCE(p.partial_fee_paid, 0) AS partial_fee,
               f.fees AS fact_fees, p.status
        FROM paper_positions p
        LEFT JOIN LATERAL (
            SELECT fees FROM trade_facts tf
            WHERE tf.position_id = p.id::text AND tf.account_id = p.account_id
            ORDER BY tf.id DESC LIMIT 1
        ) f ON TRUE
        WHERE p.account_id = :acct AND p.status IN ('closed', 'liquidated')
          AND p.closed_at IS NOT NULL AND p.closed_at >= :since
        ORDER BY p.closed_at
        """
    )
    out: List[Dict[str, Any]] = []
    for r in db.execute(sql, {"acct": int(account_id), "since": since}).mappings():
        open_size = float(r["open_size"] or 0)
        entry = float(r["entry_price"] or 0)
        close_px = float(r["close_price"] or 0) or entry
        notional = open_size * entry
        open_fee = notional * rate
        if r["fact_fees"] is not None:
            close_side_fees = float(r["fact_fees"] or 0)
            fee_source = "facts+est_open"
        else:
            close_side_fees = float(r["last_size"] or 0) * close_px * rate + float(r["partial_fee"] or 0)
            fee_source = "estimated"
        fees = open_fee + close_side_fees
        gross = float(r["gross"] or 0)
        net = gross - fees
        hold_min = None
        if r["opened_at"] and r["closed_at"]:
            try:
                hold_min = (r["closed_at"] - r["opened_at"]).total_seconds() / 60.0
            except Exception:
                hold_min = None
        out.append({
            "id": int(r["id"]), "symbol": str(r["symbol"]).upper(), "side": str(r["side"] or "").lower(),
            "tier": str(r["tier"]).lower(), "nature": str(r["nature"]).lower(),
            "strategy_id": str(r["strategy_id"] or ""), "close_reason": str(r["close_reason"] or ""),
            "close_cat": _classify_close_reason(str(r["close_reason"] or "")),
            "leverage": float(r["leverage"] or 1), "notional": notional,
            "gross": gross, "fees": fees, "net": net,
            "net_bp": (net / notional * 1e4) if notional > 0 else None,
            "hold_min": hold_min, "fee_source": fee_source, "status": str(r["status"]),
            "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
        })
    return out


def account_truth(db, account_id: int, days: int) -> Dict[str, Any]:
    from sqlalchemy import text as _t
    since = datetime.now() - timedelta(days=int(days))
    out: Dict[str, Any] = {"account_id": int(account_id)}
    try:
        b = db.execute(_t(
            "SELECT initial_balance, total_equity, available_balance, frozen_margin, realized_pnl, "
            "total_fee_paid, last_reset_at FROM paper_balances WHERE account_id = :a"
        ), {"a": int(account_id)}).mappings().first()
        if b:
            out.update({
                "initial_balance": float(b["initial_balance"] or 0),
                "total_equity": float(b["total_equity"] or 0),
                "available_balance": float(b["available_balance"] or 0),
                "frozen_margin": float(b["frozen_margin"] or 0),
                "realized_pnl": float(b["realized_pnl"] or 0),
                "total_fee_paid": float(b["total_fee_paid"] or 0),
                "last_reset_at": b["last_reset_at"].isoformat() if b["last_reset_at"] else None,
            })
    except Exception as exc:
        out["balance_error"] = str(exc)
    try:
        o = db.execute(_t(
            "SELECT COUNT(*) AS n, COALESCE(SUM(COALESCE(fee, 0)), 0) AS fees, "
            "COALESCE(SUM(CASE WHEN close_reason IS NULL THEN 1 ELSE 0 END), 0) AS opens "
            "FROM paper_orders WHERE account_id = :a AND status = 'filled' AND created_at >= :s"
        ), {"a": int(account_id), "s": since}).mappings().first()
        if o:
            out["orders_in_window"] = int(o["n"] or 0)
            out["open_orders_in_window"] = int(o["opens"] or 0)
            out["fees_in_window_truth"] = round(float(o["fees"] or 0), 4)
    except Exception as exc:
        out["orders_error"] = str(exc)
    try:
        fl = db.execute(_t(
            "SELECT COALESCE(SUM(payment), 0) FROM paper_funding_ledger "
            "WHERE account_id = :a AND settled_at >= :s"
        ), {"a": int(account_id), "s": since}).first()
        out["funding_in_window"] = round(float(fl[0] or 0), 4) if fl else 0.0
    except Exception:
        out["funding_in_window"] = None
    try:
        op = db.execute(_t(
            "SELECT COUNT(*), COALESCE(SUM(margin), 0), COALESCE(SUM(unrealized_pnl), 0) "
            "FROM paper_positions WHERE account_id = :a AND status = 'open'"
        ), {"a": int(account_id)}).first()
        out["open_positions"] = {"n": int(op[0] or 0), "margin": round(float(op[1] or 0), 4),
                                 "unrealized": round(float(op[2] or 0), 4)}
    except Exception:
        pass
    return out


def trade_facts_coverage(db, account_id: int, days: int) -> Dict[str, Any]:
    from sqlalchemy import text as _t
    since = datetime.now() - timedelta(days=int(days))
    try:
        r = db.execute(_t(
            """
            SELECT COUNT(*) AS closed,
                   COUNT(f.id) AS covered
            FROM paper_positions p
            LEFT JOIN LATERAL (
                SELECT id FROM trade_facts tf
                WHERE tf.position_id = p.id::text AND tf.account_id = p.account_id LIMIT 1
            ) f ON TRUE
            WHERE p.account_id = :a AND p.status IN ('closed', 'liquidated') AND p.closed_at >= :s
            """
        ), {"a": int(account_id), "s": since}).first()
        closed = int(r[0] or 0)
        covered = int(r[1] or 0)
        return {"closed": closed, "covered": covered, "missing": closed - covered,
                "coverage": round(covered / closed, 4) if closed else None}
    except Exception as exc:
        return {"error": str(exc)}


def shadow_lane_stats(db, days: int, *, min_n: int = 300) -> Dict[str, Any]:
    """短线影子车道：scalp_signal_log(action='shadow_fill') 的 TB 结算统计与晋升门。"""
    from sqlalchemy import text as _t
    since_ts = int((datetime.now() - timedelta(days=int(days))).timestamp())
    out: Dict[str, Any] = {"min_n": int(min_n)}
    try:
        r = db.execute(_t(
            """
            SELECT COUNT(*) AS n_total,
                   COUNT(*) FILTER (WHERE tb_settled IS TRUE AND tb_kind <> 'none') AS n_settled,
                   COUNT(*) FILTER (WHERE tb_settled IS TRUE AND tb_win IS TRUE) AS wins,
                   AVG(tb_net_ret) FILTER (WHERE tb_settled IS TRUE AND tb_kind <> 'none') AS mean_net,
                   STDDEV_SAMP(tb_net_ret) FILTER (WHERE tb_settled IS TRUE AND tb_kind <> 'none') AS sd_net,
                   COUNT(*) FILTER (WHERE tb_kind = 'tp') AS tp,
                   COUNT(*) FILTER (WHERE tb_kind = 'sl') AS sl,
                   COUNT(*) FILTER (WHERE tb_kind = 'timeout') AS timeout
            FROM scalp_signal_log
            WHERE action = 'shadow_fill' AND signal_ts >= :since
            """
        ), {"since": since_ts}).mappings().first()
        n_total = int(r["n_total"] or 0)
        n = int(r["n_settled"] or 0)
        mean_net = float(r["mean_net"] or 0)
        sd = float(r["sd_net"] or 0)
        lb = None
        if n >= 8 and sd > 0:
            lb = mean_net - _Z95 * sd / math.sqrt(n)
        out.update({
            "n_total": n_total, "n_settled": n, "wins": int(r["wins"] or 0),
            "win_rate": round(int(r["wins"] or 0) / n, 4) if n else None,
            "mean_net_bp": round(mean_net * 1e4, 2) if n else None,
            "ci95_lb_bp": round(lb * 1e4, 2) if lb is not None else None,
            "tp": int(r["tp"] or 0), "sl": int(r["sl"] or 0), "timeout": int(r["timeout"] or 0),
            "promotion_gate": {
                "n_ok": n >= int(min_n),
                "edge_ok": bool(lb is not None and lb > 0),
                "ready": bool(n >= int(min_n) and lb is not None and lb > 0),
            },
        })
    except Exception as exc:
        out["error"] = str(exc)
    return out


def compute_edge_ledger(db, account_id: int, days: int = 14, *, top_symbols: int = 12) -> Dict[str, Any]:
    """完整边际账本快照（dict，可直接 JSON 化）。"""
    rows = load_trade_rows(db, account_id, days)
    est_fees_sum = sum(r["fees"] for r in rows)
    truth = account_truth(db, account_id, days)
    truth_fees = truth.get("fees_in_window_truth")
    by_symbol = _group(rows, lambda r: r["symbol"])
    worst_symbols = sorted(
        ((k, v) for k, v in by_symbol.items() if v.get("n")), key=lambda kv: kv[1]["net"]
    )[: int(top_symbols)]
    best_symbols = sorted(
        ((k, v) for k, v in by_symbol.items() if v.get("n")), key=lambda kv: -kv[1]["net"]
    )[: int(top_symbols)]
    snapshot = {
        "account_id": int(account_id),
        "window_days": int(days),
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "fee_rate_assumed": _taker_rate(),
        "total": _stats(rows),
        "by_tier": _group(rows, lambda r: r["tier"]),
        "by_lane": _group(rows, lambda r: f"{r['tier']}|{r['side']}"),
        "by_nature": _group(rows, lambda r: r["nature"]),
        "by_close_cat": _group(rows, lambda r: r["close_cat"]),
        "by_tier_close_cat": _group(rows, lambda r: f"{r['tier']}|{r['close_cat']}"),
        "worst_symbols": [{"symbol": k, **v} for k, v in worst_symbols],
        "best_symbols": [{"symbol": k, **v} for k, v in best_symbols],
        "account_truth": truth,
        "fee_reconciliation": {
            "estimated_fees_sum": round(est_fees_sum, 4),
            "truth_fees_sum": truth_fees,
            "gap": round(float(truth_fees) - est_fees_sum, 4) if truth_fees is not None else None,
            "note": "估算含开仓费(名义×taker)；真值为窗口内 paper_orders.fee 合计（含未平仓的开仓费与被拒前成交）",
        },
        "trade_facts_coverage": trade_facts_coverage(db, account_id, days),
        "shadow_scalp": shadow_lane_stats(db, days),
    }
    return snapshot


# ── 快照落库（看板历史曲线 / 周度复盘输入）──────────────────────────────────
_TABLE_READY = False


def _ensure_snapshot_table(db) -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    from sqlalchemy import text as _t
    db.execute(_t(
        "CREATE TABLE IF NOT EXISTS edge_ledger_snapshots ("
        " id BIGSERIAL PRIMARY KEY,"
        " account_id INT NOT NULL,"
        " window_days INT NOT NULL,"
        " computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " total_n INT, total_net DOUBLE PRECISION, total_fees DOUBLE PRECISION,"
        " payload JSONB NOT NULL)"
    ))
    db.execute(_t(
        "CREATE INDEX IF NOT EXISTS ix_edge_ledger_snapshots_acct_ts "
        "ON edge_ledger_snapshots (account_id, computed_at DESC)"
    ))
    _TABLE_READY = True


def persist_snapshot(db, snapshot: Dict[str, Any]) -> Optional[int]:
    """把一份快照写入 edge_ledger_snapshots，返回 id。失败返回 None（不抛）。"""
    try:
        from sqlalchemy import text as _t
        _ensure_snapshot_table(db)
        tot = snapshot.get("total") or {}
        row = db.execute(_t(
            "INSERT INTO edge_ledger_snapshots (account_id, window_days, total_n, total_net, total_fees, payload) "
            "VALUES (:a, :w, :n, :net, :fees, CAST(:p AS JSONB)) RETURNING id"
        ), {
            "a": int(snapshot["account_id"]), "w": int(snapshot["window_days"]),
            "n": int(tot.get("n") or 0), "net": float(tot.get("net") or 0), "fees": float(tot.get("fees") or 0),
            "p": json.dumps(snapshot, ensure_ascii=False, default=str),
        }).first()
        db.commit()
        return int(row[0]) if row else None
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("[EdgeLedger] 快照落库失败: %s", exc)
        return None


def snapshot_history(db, account_id: int, limit: int = 60) -> List[Dict[str, Any]]:
    try:
        from sqlalchemy import text as _t
        _ensure_snapshot_table(db)
        rows = db.execute(_t(
            "SELECT id, window_days, computed_at, total_n, total_net, total_fees FROM edge_ledger_snapshots "
            "WHERE account_id = :a ORDER BY computed_at DESC LIMIT :lim"
        ), {"a": int(account_id), "lim": int(limit)}).mappings().all()
        return [dict(r, computed_at=r["computed_at"].isoformat() if r["computed_at"] else None) for r in rows]
    except Exception as exc:
        logger.debug("[EdgeLedger] history 读取失败: %s", exc)
        return []


def default_ledger_accounts() -> List[int]:
    """默认要出账本的账户：EDGE_LEDGER_ACCOUNTS（逗号分隔），缺省回落到有 paper_balances 的全部账户。"""
    raw = (os.getenv("EDGE_LEDGER_ACCOUNTS", "") or "").strip()
    if raw:
        out = []
        for x in raw.split(","):
            try:
                out.append(int(x.strip()))
            except ValueError:
                continue
        return out
    return []


def run_daily_snapshot_job(days: int = 14) -> Dict[str, Any]:
    """定时任务入口：为每个 paper 账户计算并落一份快照。"""
    from backend.database.connection import SessionLocal
    from sqlalchemy import text as _t
    result: Dict[str, Any] = {"accounts": [], "errors": []}
    with SessionLocal() as db:
        accounts = default_ledger_accounts()
        if not accounts:
            try:
                accounts = [int(r[0]) for r in db.execute(_t("SELECT account_id FROM paper_balances")).fetchall()]
            except Exception as exc:
                result["errors"].append(f"list accounts: {exc}")
                accounts = []
        for acct in accounts:
            try:
                snap = compute_edge_ledger(db, acct, days)
                sid = persist_snapshot(db, snap)
                tot = snap.get("total") or {}
                result["accounts"].append({"account_id": acct, "snapshot_id": sid, "n": tot.get("n"),
                                           "net": tot.get("net"), "fees": tot.get("fees")})
                logger.info("[EdgeLedger] acct=%s %dd: n=%s net=%s fees=%s verdict=%s", acct, days,
                            tot.get("n"), tot.get("net"), tot.get("fees"), tot.get("edge_verdict"))
            except Exception as exc:
                result["errors"].append(f"acct {acct}: {exc}")
                logger.warning("[EdgeLedger] acct=%s 计算失败: %s", acct, exc)
    return result
