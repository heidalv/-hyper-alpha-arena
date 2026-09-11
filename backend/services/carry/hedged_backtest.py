# -*- coding: utf-8 -*-
"""[F70] 对冲 carry 的真实数据回测：现货多 + 永续空，逐笔现金流。

与 F63 的关系：
  F63 只对**永续腿的资金费收入**做实证，现货腿按「价格盈亏完全抵消」假设处理
  （因为当时库里没有现货数据）。F70 补上真实现货行情与现货下单通道，
  两条腿各自成交、各自计费，**不再依赖抵消假设**。

数据：
  - 现货 `market_spot_klines`（Binance spot，5m，F64 采集/回填）
  - 永续 `crypto_klines`（对应场地 5m；`timestamp` 是**秒**）
  - 资金费 `perp_funding`（毫秒；按结算边界取最近样本）

回测方式：每 `step_days` 开一笔、持有 `hold_days`，滚动取样；
统计净期望（bp/笔）、胜率、分折稳定性。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from backend.services.carry.funding_model import period_hours
from backend.services.carry.hedged_carry import (
    DEFAULT_PERP_SLIPPAGE_BP,
    DEFAULT_PERP_TAKER_BP,
    DEFAULT_SPOT_SLIPPAGE_BP,
    DEFAULT_SPOT_TAKER_BP,
    simulate_position,
)

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE"]
DEFAULT_VENUE = "asterdex"
BAR_MS = 5 * 60 * 1000


def load_aligned(
    symbol: str,
    *,
    venue: str = DEFAULT_VENUE,
    days: float = 30.0,
    spot_exchange: str = "binance_spot",
    interval: str = "5m",
) -> Dict[str, np.ndarray]:
    """加载对齐后的现货/永续收盘价与时间戳（毫秒）。"""
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal

    cutoff_ms = int((time.time() - float(days) * 86400.0) * 1000)
    with system_identity():
        with MarketSessionLocal() as db:
            spot = db.execute(text(
                "SELECT ts_ms, close_price FROM market_spot_klines"
                " WHERE exchange=:e AND symbol=:s AND interval=:i AND ts_ms >= :t"
                " ORDER BY ts_ms"
            ), {"e": spot_exchange, "s": symbol, "i": interval, "t": cutoff_ms}).mappings().all()
            perp = db.execute(text(
                # crypto_klines.timestamp 是 int32（秒）；乘 1000 会溢出，必须先转 bigint
                "SELECT timestamp::bigint*1000 AS ts_ms, close_price FROM crypto_klines"
                " WHERE exchange=:e AND symbol=:s AND period=:i AND timestamp::bigint*1000 >= :t"
                " ORDER BY timestamp"
            ), {"e": venue, "s": symbol, "i": interval, "t": cutoff_ms}).mappings().all()

    sp = {int(r["ts_ms"]): float(r["close_price"]) for r in spot if r["close_price"]}
    pp = {int(r["ts_ms"]): float(r["close_price"]) for r in perp if r["close_price"]}
    common = sorted(set(sp) & set(pp))
    return {
        "ts_ms": np.array(common, dtype=np.int64),
        "spot": np.array([sp[t] for t in common], dtype=float),
        "perp": np.array([pp[t] for t in common], dtype=float),
    }


def load_funding_events(
    symbol: str,
    *,
    venue: str = DEFAULT_VENUE,
    ts_start_ms: int,
    ts_end_ms: int,
) -> List[Tuple[int, float]]:
    """按结算边界取资金费（(ts_ms, rate)），周期按场地（8h / 1h）。"""
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import MarketSessionLocal

    ph = period_hours(venue)
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(text(
                "SELECT timestamp, funding_rate FROM perp_funding"
                " WHERE exchange=:e AND symbol=:s AND timestamp BETWEEN :a AND :b"
                " ORDER BY timestamp"
            ), {"e": venue, "s": symbol, "a": int(ts_start_ms), "b": int(ts_end_ms)}
            ).mappings().all()
    samples = [(int(r["timestamp"]), float(r["funding_rate"] or 0.0)) for r in rows]
    if not samples:
        return []
    # 结算边界：按 period_hours 对齐的整点
    step = int(ph * 3600 * 1000)
    events: List[Tuple[int, float]] = []
    boundary = (int(ts_start_ms) // step + 1) * step
    idx = 0
    while boundary <= int(ts_end_ms):
        while idx + 1 < len(samples) and samples[idx + 1][0] <= boundary:
            idx += 1
        if samples[idx][0] <= boundary:
            events.append((boundary, samples[idx][1]))
        boundary += step
    return events


def backtest_symbol(
    symbol: str,
    *,
    venue: str = DEFAULT_VENUE,
    days: float = 30.0,
    hold_days: float = 7.0,
    step_days: float = 1.0,
    notional_usd: float = 1000.0,
    spot_taker_bp: float = DEFAULT_SPOT_TAKER_BP,
    perp_taker_bp: float = DEFAULT_PERP_TAKER_BP,
    spot_slippage_bp: float = DEFAULT_SPOT_SLIPPAGE_BP,
    perp_slippage_bp: float = DEFAULT_PERP_SLIPPAGE_BP,
) -> Dict[str, Any]:
    """单币滚动回测（每 step_days 开一笔，持有 hold_days）。"""
    data = load_aligned(symbol, venue=venue, days=days)
    ts, sp, pp = data["ts_ms"], data["spot"], data["perp"]
    n = len(ts)
    if n < 100:
        return {"symbol": symbol, "venue": venue, "error": "数据不足",
                "bars": n, "positions": []}

    hold_bars = int(max(1, round(hold_days * 86400_000 / BAR_MS)))
    step_bars = int(max(1, round(step_days * 86400_000 / BAR_MS)))
    hold_ms = int(hold_days * 86400_000)
    # 按**时间戳**定位出场点，而不是按索引：5m 数据有约 18% 缺口，
    # 按索引会因缺口而把 30 天持有「挤」到窗口外，导致样本莫名变少。
    positions: List[Dict[str, Any]] = []
    for i in range(0, n - 1, step_bars):
        target = int(ts[i]) + hold_ms
        j = int(np.searchsorted(ts, target, side="left"))
        if j >= n:
            break
        if abs(int(ts[j]) - target) > BAR_MS:      # 出场点偏离超过一根 K 线 → 跳过
            continue
        events = load_funding_events(symbol, venue=venue,
                                     ts_start_ms=int(ts[i]), ts_end_ms=int(ts[j]))
        sim = simulate_position(
            symbol=symbol, venue=venue,
            spot_px0=float(sp[i]), perp_px0=float(pp[i]),
            spot_px1=float(sp[j]), perp_px1=float(pp[j]),
            ts0=float(ts[i]) / 1000.0, ts1=float(ts[j]) / 1000.0,
            notional_usd=notional_usd,
            funding_events=[(float(pp[i]), rate) for _ts, rate in events],
            spot_taker_bp=spot_taker_bp, perp_taker_bp=perp_taker_bp,
            spot_slippage_bp=spot_slippage_bp, perp_slippage_bp=perp_slippage_bp,
        )
        positions.append(sim.to_dict())

    if not positions:
        return {"symbol": symbol, "venue": venue, "error": "无有效样本",
                "bars": n, "positions": []}

    nets = np.array([p["net_bp"] for p in positions], dtype=float)
    funds = np.array([p["funding_usd"] for p in positions], dtype=float)
    basis = np.array([p["basis_entry_bp"] - p["basis_exit_bp"] for p in positions], dtype=float)
    t = float(nets.mean() / (nets.std(ddof=1) / np.sqrt(len(nets)))) \
        if len(nets) > 1 and nets.std() > 0 else 0.0
    # 分折
    folds = []
    k = 4
    if len(positions) >= k * 2:
        size = len(positions) // k
        for f in range(k):
            chunk = nets[f * size:(f + 1) * size] if f < k - 1 else nets[f * size:]
            folds.append({
                "fold": f, "n": int(len(chunk)),
                "net_bp": round(float(chunk.mean()), 4),
                "t": round(float(chunk.mean() / (chunk.std(ddof=1) / np.sqrt(len(chunk))))
                           if len(chunk) > 1 and chunk.std() > 0 else 0.0, 3),
            })
    return {
        "symbol": symbol, "venue": venue, "bars": n,
        "n_positions": len(positions),
        "window_days": round((int(ts[-1]) - int(ts[0])) / 86400_000, 2) if n else 0.0,
        "hold_days": hold_days, "step_days": step_days,
        "net_bp_mean": round(float(nets.mean()), 4),
        "net_bp_median": round(float(np.median(nets)), 4),
        "net_bp_std": round(float(nets.std(ddof=1)), 4) if len(nets) > 1 else 0.0,
        "t": round(t, 3),
        "win_ratio": round(float((nets > 0).mean()), 4),
        "funding_usd_mean": round(float(funds.mean()), 4),
        "basis_convergence_bp_mean": round(float(basis.mean()), 4),
        "net_usd_mean": round(float(np.mean([p["net_usd"] for p in positions])), 4),
        "folds": folds,
        "sample": positions[:3],
    }


def backtest(
    symbols: Optional[List[str]] = None,
    *,
    venue: str = DEFAULT_VENUE,
    days: float = 30.0,
    hold_days: float = 7.0,
    step_days: float = 1.0,
    **kw,
) -> Dict[str, Any]:
    """全部标的的滚动回测汇总（组合级 = 等权平均）。"""
    syms = symbols or DEFAULT_SYMBOLS
    per = [backtest_symbol(s, venue=venue, days=days, hold_days=hold_days,
                           step_days=step_days, **kw) for s in syms]
    ok = [p for p in per if p.get("n_positions")]
    means = [p["net_bp_mean"] for p in ok]
    combo = round(float(np.mean(means)), 4) if means else 0.0
    return {
        "venue": venue, "days": days, "hold_days": hold_days, "step_days": step_days,
        "symbols": per,
        "combo_net_bp": combo,
        "positive_symbols": sum(1 for m in means if m > 0),
        "n_symbols": len(ok),
        "note": "现货多 + 永续空，逐笔现金流（含两条腿 taker 费率与滑点）",
    }


def format_report(res: Dict[str, Any]) -> str:
    lines = [f"[F70] 对冲 carry 回测 场地={res['venue']} 窗口={res['days']:.0f}天"
             f" 持有={res['hold_days']:.1f}天 每 {res['step_days']:.0f}天开一笔"]
    lines.append(f"  {'symbol':<7} {'样本':>5} {'净bp/笔':>9} {'中位':>8} {'t':>6}"
                 f" {'胜率':>6} {'资金费$':>9} {'基差收敛bp':>11} {'分折'}")
    for p in res["symbols"]:
        if not p.get("n_positions"):
            lines.append(f"  {p['symbol']:<7} {'—':>5}  {p.get('error')}")
            continue
        folds = " ".join(f"{f['net_bp']:+.1f}" for f in p.get("folds", []))
        lines.append(f"  {p['symbol']:<7} {p['n_positions']:>5} {p['net_bp_mean']:>+9.3f}"
                     f" {p['net_bp_median']:>+8.3f} {p['t']:>6.2f} {p['win_ratio']*100:>5.1f}%"
                     f" {p['funding_usd_mean']:>+9.3f} {p['basis_convergence_bp_mean']:>+11.3f}"
                     f"  {folds}")
    lines.append(f"  组合等权净期望: {res['combo_net_bp']:+.3f}bp/笔"
                 f"（{res['positive_symbols']}/{res['n_symbols']} 标的为正）")
    return "\n".join(lines)