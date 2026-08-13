"""短线因子每日体检（只读任务）。

设计文档《短线因子交易整改设计_2026-08-11.md》Phase 0 交付：
- 信号级：总量/已结算/胜率/平均净收益/分数分桶+单调性
- 交易级：纸盘 scalp 平仓原因/符号/小时段归因、成本占比
- 因子级：运行时权重分布 + Gini
- 模型级：meta 报告摘要
- 数据级：5m K 线新鲜度

输出：reports/scalp_daily/scalp_health_YYYY-MM-DD.json / .md
不写任何交易表，不改任何交易行为；RLS 走 system_identity。
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _round(v: Any, nd: int = 4) -> Any:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, nd)
    except Exception:
        return None


def _spearman_rank(xs: List[float], ys: List[float]):
    """返回 (rho, p_value)。优先 scipy，缺失时用秩相关近似。"""
    try:
        from scipy.stats import spearmanr
        rho, p = spearmanr(xs, ys)
        return float(rho), float(p)
    except Exception:
        pass
    n = len(xs)
    if n < 3:
        return 0.0, 1.0

    def _rank(vals: List[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx = _rank(xs)
    ry = _rank(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return 0.0, 1.0
    rho = num / (dx * dy)
    # 近似 t 检验
    try:
        t = rho * math.sqrt((n - 2) / max(1e-9, 1 - rho * rho))
        from scipy.stats import t as _t
        p = 2.0 * (1.0 - _t.cdf(abs(t), n - 2))
    except Exception:
        p = 1.0
    return float(rho), float(p)


def _gini(values: List[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return 0.0
    vals.sort()
    n = len(vals)
    cum = 0.0
    for i, v in enumerate(vals, start=1):
        cum += (2.0 * i - n - 1.0) * v
    mean = sum(vals) / n
    if mean == 0:
        return 0.0
    return max(0.0, min(1.0, cum / (n * n * mean)))


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("[ScalpHealth] 读取 %s 失败: %s", path, e)
        return None


def _signal_stats(days: int) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    start_ts = int((_now_utc() - timedelta(days=days)).timestamp())
    buckets = [
        ("<30", 0.0, 30.0),
        ("30-40", 30.0, 40.0),
        ("40-50", 40.0, 50.0),
        ("50-60", 50.0, 60.0),
        (">=60", 60.0, None),
    ]
    out: Dict[str, Any] = {"days": days, "buckets": [], "monotonicity": {}}
    with system_identity():
        with SessionLocal() as db:
            total = db.execute(
                text(
                    "SELECT COUNT(*) AS n, "
                    "COUNT(*) FILTER (WHERE settled AND win IS NOT NULL) AS n_settled, "
                    "COUNT(*) FILTER (WHERE win) AS n_win, "
                    "COALESCE(AVG(net_ret) FILTER (WHERE settled AND win IS NOT NULL), 0) AS avg_net "
                    "FROM scalp_signal_log WHERE signal_ts >= :start"
                ),
                {"start": start_ts},
            ).mappings().first()
            if total:
                n = int(total["n"] or 0)
                n_settled = int(total["n_settled"] or 0)
                n_win = int(total["n_win"] or 0)
                avg_net = float(total["avg_net"] or 0.0)
                out["total"] = n
                out["settled"] = n_settled
                out["win_rate"] = _round(n_win / n_settled if n_settled else 0.0, 4)
                out["avg_net_ret"] = _round(avg_net, 6)

            midpoints: List[float] = []
            win_rates: List[float] = []
            net_rets: List[float] = []
            for label, lo, hi in buckets:
                if hi is None:
                    row = db.execute(
                        text(
                            "SELECT COUNT(*) AS n, "
                            "COUNT(*) FILTER (WHERE win) AS n_win, "
                            "COALESCE(AVG(net_ret), 0) AS avg_net "
                            "FROM scalp_signal_log "
                            "WHERE signal_ts >= :start AND settled AND win IS NOT NULL "
                            "AND factor_score >= :lo"
                        ),
                        {"start": start_ts, "lo": lo},
                    ).mappings().first()
                else:
                    row = db.execute(
                        text(
                            "SELECT COUNT(*) AS n, "
                            "COUNT(*) FILTER (WHERE win) AS n_win, "
                            "COALESCE(AVG(net_ret), 0) AS avg_net "
                            "FROM scalp_signal_log "
                            "WHERE signal_ts >= :start AND settled AND win IS NOT NULL "
                            "AND factor_score >= :lo AND factor_score < :hi"
                        ),
                        {"start": start_ts, "lo": lo, "hi": hi},
                    ).mappings().first()
                if not row:
                    continue
                n_b = int(row["n"] or 0)
                n_w = int(row["n_win"] or 0)
                wr = n_w / n_b if n_b else 0.0
                nr = float(row["avg_net"] or 0.0)
                out["buckets"].append({
                    "bucket": label,
                    "n": n_b,
                    "win_rate": _round(wr, 4),
                    "avg_net_ret": _round(nr, 6),
                })
                midpoints.append((lo + hi) / 2 if hi is not None else lo + 5.0)
                win_rates.append(wr)
                net_rets.append(nr)

            if len(midpoints) >= 3:
                rho_w, p_w = _spearman_rank(midpoints, win_rates)
                rho_n, p_n = _spearman_rank(midpoints, net_rets)
                out["monotonicity"] = {
                    "win_rate_rho": _round(rho_w, 4),
                    "win_rate_p": _round(p_w, 5),
                    "net_ret_rho": _round(rho_n, 4),
                    "net_ret_p": _round(p_n, 5),
                }
    return out


def _paper_stats(days: int) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    start = _now_utc() - timedelta(days=days)
    out: Dict[str, Any] = {
        "trades": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "net_pnl": 0.0,
        "avg_hold_hours": None,
        "fee_sum": 0.0,
        "cost_bps": None,
        "by_close_reason": [],
        "by_symbol": [],
        "by_hour": [],
    }
    with system_identity():
        with SessionLocal() as db:
            base = db.execute(
                text(
                    "SELECT COUNT(*) AS n, "
                    "COUNT(*) FILTER (WHERE pnl > 0) AS n_win, "
                    "COALESCE(SUM(pnl), 0) AS net_pnl, "
                    "COALESCE(SUM(fee), 0) AS fee_sum, "
                    "COALESCE(SUM(pnl) FILTER (WHERE pnl > 0), 0) AS gross_win, "
                    "COALESCE(SUM(pnl) FILTER (WHERE pnl < 0), 0) AS gross_loss, "
                    "COALESCE(SUM(quantity * COALESCE(filled_price, price)), 0) AS notional, "
                    "AVG(EXTRACT(EPOCH FROM (filled_at - created_at))) AS avg_hold_sec "
                    "FROM paper_orders "
                    "WHERE trade_nature = 'scalp' AND status = 'filled' "
                    "AND pnl IS NOT NULL AND created_at >= :start"
                ),
                {"start": start},
            ).mappings().first()
            if not base:
                return out
            n = int(base["n"] or 0)
            n_win = int(base["n_win"] or 0)
            net = float(base["net_pnl"] or 0.0)
            fee = float(base["fee_sum"] or 0.0)
            notional = float(base["notional"] or 0.0)
            gross_win = float(base["gross_win"] or 0.0)
            gross_loss = float(base["gross_loss"] or 0.0)
            hold_sec = base["avg_hold_sec"]
            out.update({
                "trades": n,
                "win_rate": _round(n_win / n if n else 0.0, 4),
                "profit_factor": _round(gross_win / abs(gross_loss), 4) if gross_loss else None,
                "net_pnl": _round(net, 4),
                "avg_hold_hours": _round(float(hold_sec) / 3600.0, 3) if hold_sec is not None else None,
                "fee_sum": _round(fee, 4),
                "cost_bps": _round(fee / notional * 1e4, 4) if notional else None,
            })

            rows = db.execute(
                text(
                    "SELECT COALESCE(NULLIF(close_reason, ''), 'unknown') AS reason, "
                    "COUNT(*) AS n, COALESCE(SUM(pnl), 0) AS pnl, "
                    "COALESCE(AVG(pnl), 0) AS avg_pnl "
                    "FROM paper_orders "
                    "WHERE trade_nature = 'scalp' AND status = 'filled' "
                    "AND pnl IS NOT NULL AND created_at >= :start "
                    "GROUP BY 1 ORDER BY pnl ASC"
                ),
                {"start": start},
            ).mappings().all()
            out["by_close_reason"] = [
                {
                    "reason": r["reason"],
                    "n": int(r["n"] or 0),
                    "net_pnl": _round(r["pnl"], 4),
                    "avg_pnl": _round(r["avg_pnl"], 4),
                }
                for r in rows
            ]

            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(*) AS n, COALESCE(SUM(pnl), 0) AS pnl, "
                    "COALESCE(AVG(pnl), 0) AS avg_pnl "
                    "FROM paper_orders "
                    "WHERE trade_nature = 'scalp' AND status = 'filled' "
                    "AND pnl IS NOT NULL AND created_at >= :start "
                    "GROUP BY symbol ORDER BY pnl ASC LIMIT 20"
                ),
                {"start": start},
            ).mappings().all()
            out["by_symbol"] = [
                {
                    "symbol": r["symbol"],
                    "n": int(r["n"] or 0),
                    "net_pnl": _round(r["pnl"], 4),
                    "avg_pnl": _round(r["avg_pnl"], 4),
                }
                for r in rows
            ]

            rows = db.execute(
                text(
                    "SELECT EXTRACT(HOUR FROM filled_at) AS hh, COUNT(*) AS n, "
                    "COALESCE(SUM(pnl), 0) AS pnl "
                    "FROM paper_orders "
                    "WHERE trade_nature = 'scalp' AND status = 'filled' "
                    "AND pnl IS NOT NULL AND created_at >= :start "
                    "GROUP BY 1 ORDER BY 1"
                ),
                {"start": start},
            ).mappings().all()
            out["by_hour"] = [
                {
                    "hour_utc": int(r["hh"] or 0),
                    "n": int(r["n"] or 0),
                    "net_pnl": _round(r["pnl"], 4),
                }
                for r in rows
            ]
    return out


def _factor_weight_stats() -> Dict[str, Any]:
    data = _read_json(_ROOT / "data" / "factor_runtime_weights.json")
    if not data:
        return {"available": False}
    weights = data.get("weights")
    if not isinstance(weights, dict):
        return {"available": False}
    vals = []
    for v in weights.values():
        try:
            f = float(v)
            if math.isfinite(f):
                vals.append(f)
        except Exception:
            continue
    if not vals:
        return {"available": False}
    vals_sorted = sorted(vals)
    n = len(vals_sorted)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, int(p * n)))
        return vals_sorted[idx]

    return {
        "available": True,
        "updated_at": data.get("updated_at"),
        "n_weights": n,
        "p25": _round(_pct(0.25), 4),
        "p50": _round(_pct(0.50), 4),
        "p75": _round(_pct(0.75), 4),
        "p90": _round(_pct(0.90), 4),
        "n_in_05_10": sum(1 for v in vals if 0.5 <= v <= 1.0),
        "n_above_10": sum(1 for v in vals if v > 1.0),
        "gini": _round(_gini(vals), 4),
    }


def _meta_summary() -> Dict[str, Any]:
    data = _read_json(_ROOT / "data" / "scalp_meta_report.json")
    if not data:
        return {"available": False}
    return {
        "available": True,
        "usable": bool(data.get("usable")),
        "oos_auc_lgbm": _round(data.get("oos_auc_lgbm"), 4),
        "oos_auc_linear": _round(data.get("oos_auc_linear"), 4),
        "baseline_win_rate": _round(
            (data.get("baseline") or {}).get("win_rate"), 4
        ),
        "baseline_net_ret": _round(
            (data.get("baseline") or {}).get("net_ret"), 6
        ),
        "filter_top30_win_rate": _round(
            (data.get("filter_top30pct") or {}).get("win_rate"), 4
        ),
        "filter_top30_net_ret": _round(
            (data.get("filter_top30pct") or {}).get("net_ret"), 6
        ),
        "gate_reasons": data.get("gate_reasons") or [],
    }


def _kline_freshness(top_symbols: List[str]) -> Dict[str, Any]:
    from backend.database.connection import MarketSessionLocal
    from backend.core.tenant import system_identity

    out: Dict[str, Any] = {"period": "5m", "by_symbol": {}}
    with system_identity():
        with MarketSessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT MAX(timestamp) AS max_ts, COUNT(*) AS n "
                    "FROM crypto_klines WHERE period = '5m'"
                )
            ).mappings().first()
            if row:
                out["global_max_ts"] = int(row["max_ts"] or 0)
                out["global_count"] = int(row["n"] or 0)
            now = int(_now_utc().timestamp())
            out["freshness_sec"] = (
                now - out.get("global_max_ts", 0) if out.get("global_max_ts") else None
            )
            for sym in top_symbols[:10]:
                row = db.execute(
                    text(
                        "SELECT MAX(timestamp) AS max_ts, COUNT(*) AS n "
                        "FROM crypto_klines WHERE period = '5m' AND symbol = :sym"
                    ),
                    {"sym": sym},
                ).mappings().first()
                if row and row["max_ts"]:
                    out["by_symbol"][sym] = {
                        "max_ts": int(row["max_ts"]),
                        "freshness_sec": now - int(row["max_ts"]),
                        "count": int(row["n"] or 0),
                    }
    return out


def _top_symbols(days: int) -> List[str]:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity

    start_ts = int((_now_utc() - timedelta(days=days)).timestamp())
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT symbol, COUNT(*) AS n FROM scalp_signal_log "
                    "WHERE signal_ts >= :start GROUP BY symbol ORDER BY n DESC LIMIT 10"
                ),
                {"start": start_ts},
            ).mappings().all()
    return [str(r["symbol"]) for r in rows]


def _render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Scalp 每日体检（%s）" % report["date"],
        "",
        "## 信号级",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 信号总数 | %s |" % report["signal"].get("total"),
        "| 已结算 | %s |" % report["signal"].get("settled"),
        "| 胜率 | %.2f%% |" % (report["signal"].get("win_rate", 0) * 100),
        "| 平均净收益 | %s%% |" % (report["signal"].get("avg_net_ret", 0) * 100),
        "",
        "### 分数分桶",
        "",
        "| 桶 | 信号数 | 胜率 | 平均净收益 |",
        "|---|---|---|---|",
    ]
    for b in report["signal"].get("buckets", []):
        lines.append(
            "| %s | %s | %.2f%% | %s%% |"
            % (
                b["bucket"],
                b["n"],
                (b["win_rate"] or 0) * 100,
                (b["avg_net_ret"] or 0) * 100,
            )
        )
    mono = report["signal"].get("monotonicity") or {}
    if mono:
        lines += [
            "",
            "单调性：胜率 rho=%s (p=%s)，净收益 rho=%s (p=%s)"
            % (
                mono.get("win_rate_rho"),
                mono.get("win_rate_p"),
                mono.get("net_ret_rho"),
                mono.get("net_ret_p"),
            ),
        ]
    lines += ["", "## 纸盘交易级", ""]
    p = report["paper"]
    lines += [
        "| 指标 | 值 |",
        "|---|---|",
        "| 已平笔数 | %s |" % p.get("trades"),
        "| 胜率 | %.2f%% |" % (p.get("win_rate", 0) * 100),
        "| 利润因子 | %s |" % p.get("profit_factor"),
        "| 净盈亏 | %s USD |" % p.get("net_pnl"),
        "| 平均持仓 | %s h |" % p.get("avg_hold_hours"),
        "| 费用 | %s USD |" % p.get("fee_sum"),
        "| 成本 | %s bps |" % p.get("cost_bps"),
        "",
        "### 平仓原因",
        "",
        "| 原因 | 笔数 | 净盈亏 | 均值/笔 |",
        "|---|---|---|---|",
    ]
    for r in p.get("by_close_reason", []):
        lines.append(
            "| %s | %s | %s | %s |"
            % (r["reason"], r["n"], r["net_pnl"], r["avg_pnl"])
        )
    lines += ["", "### 符号 TOP（亏损侧）", "", "| 符号 | 笔数 | 净盈亏 | 均值/笔 |", "|---|---|---|---|"]
    for r in p.get("by_symbol", [])[:10]:
        lines.append(
            "| %s | %s | %s | %s |"
            % (r["symbol"], r["n"], r["net_pnl"], r["avg_pnl"])
        )
    lines += ["", "## 因子权重", ""]
    w = report.get("weights") or {}
    if w.get("available"):
        lines.append(
            "- 权重数 %s，Gini %s，[0.5,1.0] %s 个，>1.0 %s 个"
            % (w["n_weights"], w["gini"], w["n_in_05_10"], w["n_above_10"])
        )
    lines += ["", "## Meta", ""]
    m = report.get("meta") or {}
    if m.get("available"):
        lines.append(
            "- usable=%s，OOS AUC LGBM=%s / 线性=%s，top30 过滤净收益=%s%%"
            % (
                m.get("usable"),
                m.get("oos_auc_lgbm"),
                m.get("oos_auc_linear"),
                (m.get("filter_top30_net_ret") or 0) * 100,
            )
        )
    lines += ["", "## 数据新鲜度", ""]
    k = report.get("kline") or {}
    lines.append(
        "- 5m 最新时间戳 %s，新鲜度 %s 秒，总行数 %s"
        % (k.get("global_max_ts"), k.get("freshness_sec"), k.get("global_count"))
    )
    return "\n".join(lines)


def run_scalp_daily_health(days: int = 30) -> Dict[str, Any]:
    """生成当日体检报告并落盘。返回报告 dict（幂等，可重复跑）。"""
    today = _now_utc().strftime("%Y-%m-%d")
    top_symbols = _top_symbols(days)
    report: Dict[str, Any] = {
        "date": today,
        "generated_at": _now_utc().isoformat(),
        "days": days,
        "signal": _signal_stats(days),
        "paper": _paper_stats(days),
        "weights": _factor_weight_stats(),
        "meta": _meta_summary(),
        "kline": _kline_freshness(top_symbols),
    }
    out_dir = _ROOT / "reports" / "scalp_daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / ("scalp_health_%s.json" % today)
    md_path = out_dir / ("scalp_health_%s.md" % today)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    logger.info(
        "[ScalpHealth] 已生成 %s / %s",
        json_path,
        md_path,
    )
    try:
        from backend.services.scalp.scalp_heartbeat import touch
        touch("scalp_daily_health", "ok", {
            "signal_total": report["signal"].get("total"),
            "paper_trades": report["paper"].get("trades"),
        })
    except Exception:
        pass
    return report


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(_ROOT))

    ap = argparse.ArgumentParser(description="scalp 每日体检")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    rep = run_scalp_daily_health(days=args.days)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
