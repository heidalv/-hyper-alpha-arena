# -*- coding: utf-8 -*-
"""[F63] carry 回测：用**真实资金费历史**判断现货-永续 carry 是否可行。

重要前提（诚实声明）：
  库里**没有现货数据**（`crypto_klines.market` 恒为 'CRYPTO'，无 spot 标识；
  `crypto_prices` 空表）。因此本回测只对**永续腿的资金费收入**做实证，
  现货腿的对冲效果按「价格盈亏完全抵消、只留成本与基差」处理。
  真正落地还需要：① 现货行情采集器；② 现货下单通道（设计文档 §1.3）。

回测内容：
  1. 每个 (venue, symbol) 的 8h 资金费分布（均值/中位数/为正比例/t）；
  2. 覆盖一次进出成本（永续 taker 往返 8bp + 现货往返 10bp）所需持有期；
  3. 在 7 / 14 / 30 / 60 天持有假设下的净收益（bp 与每千美元美元数）；
  4. 分折稳健性（把历史切成 N 折，逐折均值与 t）——避免「一段行情定胜负」。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.services.carry.funding_model import (
    DEFAULT_PERP_ROUND_TRIP_BP,
    DEFAULT_SPOT_ROUND_TRIP_BP,
    carry_metrics,
    funding_stats,
    periods_per_day,
)
from backend.services.carry import funding_model as fm

logger = logging.getLogger(__name__)

DEFAULT_VENUES = ("asterdex", "binance", "hyperliquid", "bybit")
DEFAULT_SYMBOLS = ("BTC", "ETH", "BNB", "XRP", "SOL", "DOGE")
HOLD_DAYS = (7.0, 14.0, 30.0, 60.0)


def _load_funding(
    venue: str,
    symbols: Optional[List[str]] = None,
    days: float = 90.0,
) -> Dict[str, List[float]]:
    """读取资金费历史（bp）。`perp_funding.timestamp` 是**毫秒**。"""
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal

    cutoff_ms = int((__import__("time").time() - days * 86400.0) * 1000)
    params: Dict[str, Any] = {"e": venue, "ts": cutoff_ms}
    where = "WHERE exchange=:e AND timestamp >= :ts"
    syms = list(symbols or DEFAULT_SYMBOLS)
    if syms:
        where += " AND symbol = ANY(:syms)"
        params["syms"] = syms
    try:
        with system_identity():
            with MarketSessionLocal() as db:
                rows = db.execute(text(
                    "SELECT symbol, funding_rate FROM perp_funding"
                    f" {where} ORDER BY symbol, timestamp"
                ), params).mappings().all()
    except Exception as e:
        logger.warning("[F63] 读取资金费失败: %s", e)
        return {}
    out: Dict[str, List[float]] = {}
    for r in rows:
        out.setdefault(str(r["symbol"]), []).append(float(r["funding_rate"] or 0.0) * 1e4)
    return out


def _folds(xs: List[float], n_folds: int = 4) -> List[Dict[str, Any]]:
    if len(xs) < n_folds * 8:
        return []
    size = len(xs) // n_folds
    out = []
    for i in range(n_folds):
        chunk = xs[i * size:(i + 1) * size] if i < n_folds - 1 else xs[i * size:]
        st = funding_stats(chunk)
        out.append({"fold": i, "n": st["n"], "mean_bp": st["mean_bp"], "t": st["t"]})
    return out


def backtest_symbol(
    symbol: str,
    rates_bp: List[float],
    *,
    cost_bp: float = DEFAULT_PERP_ROUND_TRIP_BP + DEFAULT_SPOT_ROUND_TRIP_BP,
    hold_days: tuple = HOLD_DAYS,
    venue: str = "",
) -> Dict[str, Any]:
    """单币 carry 回测（纯计算，便于单测）。"""
    st = funding_stats(rates_bp)
    holds = {f"{int(d)}d": carry_metrics(avg_funding_bp=st["mean_bp"], cost_bp=cost_bp,
                                         hold_days=d, venue=venue)
             for d in hold_days}
    best = max(holds.items(), key=lambda kv: kv[1]["net_bp"]) if holds else (None, {})
    return {
        "symbol": symbol,
        "venue": venue,
        "period_hours": fm.period_hours(venue),
        "periods_per_day": fm.periods_per_day(venue),
        "stats": st,
        "cost_bp": round(cost_bp, 3),
        "holds": holds,
        "best_hold": best[0],
        "best_net_bp": best[1].get("net_bp"),
        "breakeven_days": holds.get("7d", {}).get("breakeven_days"),
        "folds": _folds(rates_bp),
    }


def backtest(
    *,
    venues: tuple = DEFAULT_VENUES,
    symbols: Optional[List[str]] = None,
    days: float = 90.0,
    cost_bp: Optional[float] = None,
) -> Dict[str, Any]:
    """全部场地/标的的 carry 回测汇总。"""
    cost = (DEFAULT_PERP_ROUND_TRIP_BP + DEFAULT_SPOT_ROUND_TRIP_BP
            if cost_bp is None else float(cost_bp))
    out: Dict[str, Any] = {
        "days": days, "cost_bp": cost,
        "cost_breakdown": {"perp_round_trip_bp": DEFAULT_PERP_ROUND_TRIP_BP,
                           "spot_round_trip_bp": DEFAULT_SPOT_ROUND_TRIP_BP},
        "venues": {}, "note": "现货腿无行情数据，价格盈亏按完全对冲处理（仅计成本与基差）",
    }
    for v in venues:
        data = _load_funding(v, symbols, days)
        if not data:
            out["venues"][v] = {"error": "无资金费数据"}
            continue
        per = {s: backtest_symbol(s, xs, cost_bp=cost, venue=v)
               for s, xs in sorted(data.items())}
        # 组合级：等权持有全部标的，按 30 天口径（周期口径随场地）
        means = [p["stats"]["mean_bp"] for p in per.values() if p["stats"]["n"] > 0]
        combo = carry_metrics(avg_funding_bp=(sum(means) / len(means) if means else 0.0),
                              cost_bp=cost, hold_days=30.0, venue=v)
        out["venues"][v] = {
            "symbols": per,
            "combo_30d": combo,
            "executable_symbols": sorted(
                s for s, p in per.items()
                if (p["holds"].get("30d") or {}).get("profitable")
            ),
        }
    return out


def format_report(res: Dict[str, Any]) -> str:
    """把回测结果渲染成可读表格（供 CLI/报告使用）。"""
    lines: List[str] = []
    cost = res.get("cost_bp", 0.0)
    lines.append(f"carry 回测：窗口 {res.get('days')} 天，一次性成本 {cost:.1f}bp"
                 f"（永续往返 {res['cost_breakdown']['perp_round_trip_bp']:.0f} +"
                 f" 现货往返 {res['cost_breakdown']['spot_round_trip_bp']:.0f}）")
    for v, d in res.get("venues", {}).items():
        if "error" in d:
            lines.append(f"\n[{v}] {d['error']}")
            continue
        lines.append(f"\n[{v}]  结算周期 {fm.period_hours(v):.0f}h（{periods_per_day(v):.0f} 期/天）")
        lines.append(f"  {'symbol':<8} {'n':>5} {'mean_bp':>9} {'pos%':>6} {'t':>6}"
                     f" {'be_days':>8} {'30d_net_bp':>11} {'30d_usd/k':>10}")
        for s, p in d["symbols"].items():
            st = p["stats"]
            h30 = p["holds"].get("30d", {})
            be = p["breakeven_days"]
            lines.append(
                f"  {s:<8} {st['n']:>5} {st['mean_bp']:>9.4f}"
                f" {st['positive_ratio']*100:>5.1f}% {st['t']:>6.2f}"
                f" {('—' if be is None else f'{be:>8.1f}')}"
                f" {h30.get('net_bp', 0):>11.3f} {h30.get('net_usd_per_1k', 0):>10.3f}"
            )
        c = d.get("combo_30d", {})
        lines.append(f"  组合(30d): 净 {c.get('net_bp', 0):+.3f}bp /"
                     f" ${c.get('net_usd_per_1k', 0):+.3f} 每千美元"
                     f" → {'可执行' if c.get('profitable') else '不可执行'}")
        lines.append(f"  30d 为正的标的: {d.get('executable_symbols') or '无'}")
    return "\n".join(lines)
