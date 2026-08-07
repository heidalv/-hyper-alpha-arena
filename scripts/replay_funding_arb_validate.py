"""资金费套利历史回放验证（Phase 4）。

2026-07-06 新增：用真实历史资金费率（perp_funding 表）回放验证"delta-neutral 资金费
捕获扣成本后是否真的正 EV"，对齐 scalp walk-forward 的诚实风格——宁可暴露短板。

【重要数据限制（诚实声明）】
    本环境 perp_funding 表**只有 hyperliquid 一个交易所**的历史资金费（约 130 万行、
    66 个 symbol）。因此**无法回放真正的"跨所资金费价差"**（需要至少两个场所同一时刻
    的资金费）。本脚本退而验证一个更基础、也更保守的问题：

        "在 HL 上持有【资金费收取方】方向的仓位（正费率则做空、负费率则做多），
         对冲腿假设资金费=0（保守），扣掉两腿开+平手续费后，是否还是正 EV？"

    这回答了 delta-neutral 载体里**最关键的资金费腿**能否覆盖成本；跨所价差的额外收益
    只会让结果更好。真正的跨所验证需先补另一场所的 perp_funding 历史（Phase 5 数据接入）。

用法：
    python scripts/replay_funding_arb_validate.py [--symbols N] [--min-rows M] [--notional U]

输出：每 symbol 的 年化毛资金费 / 扣成本净 APR / 保本天数 / 正 EV 窗口占比，及总体结论。
"""

import argparse
import os
import statistics
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal  # noqa: E402
from backend.services.rebate_arb import program_registry as pr  # noqa: E402

DAYS_PER_YEAR = 365.0
HOURS_PER_YEAR = 24 * DAYS_PER_YEAR


def _load_funding_series(db, exchange: str, symbol: str) -> List[Tuple[int, float]]:
    rows = db.execute(
        text(
            "SELECT timestamp, funding_rate FROM perp_funding "
            "WHERE exchange=:ex AND symbol=:sym ORDER BY timestamp ASC"
        ),
        {"ex": exchange, "sym": symbol},
    ).fetchall()
    return [(int(r[0]), float(r[1])) for r in rows]


# perp_funding 存的是"当前小时资金费率"的高频快照（~15s 一行），不是逐次结算事件。
# 因此绝不能"每行当一次结算"累加（会高估上百倍）。正确做法：把小时费率沿真实
# 流逝时间积分——每步实现资金费 ≈ rate(小时费率) × Δt(小时)。单步 Δt 封顶 1h，
# 避免大间隔用一个陈旧费率外推。
MAX_STEP_HOURS = 1.0


def _integrate_funding(series: List[Tuple[int, float]]) -> List[Tuple[float, float]]:
    """把 (ts_ms, hourly_rate) 快照序列转为 (dt_hours, rate) 步长序列。"""
    steps: List[Tuple[float, float]] = []
    for i in range(1, len(series)):
        dt_h = (series[i][0] - series[i - 1][0]) / 3_600_000.0
        if dt_h <= 0:
            continue
        dt_h = min(dt_h, MAX_STEP_HOURS)
        steps.append((dt_h, series[i - 1][1]))  # 用区间起点费率
    return steps


def _top_symbols(db, exchange: str, limit: int, min_rows: int) -> List[str]:
    rows = db.execute(
        text(
            "SELECT symbol, COUNT(*) c FROM perp_funding WHERE exchange=:ex "
            "GROUP BY symbol HAVING COUNT(*) >= :mr ORDER BY c DESC LIMIT :lim"
        ),
        {"ex": exchange, "mr": min_rows, "lim": limit},
    ).fetchall()
    return [r[0] for r in rows]


def validate_symbol(
    series: List[Tuple[int, float]],
    symbol: str,
    exchange: str,
    notional_usd: float,
    window_days: float,
) -> Dict:
    """对单 symbol 回放资金费捕获（持单一方向）扣成本净 EV。

    模型：把小时费率沿真实时间积分。持"整个窗口方向不变"的仓位（不作弊逐时翻面），
    窗口实现资金费 = |Σ rate×dt|（沿窗口净有利方向），扣一次性两腿开平手续费。
    """
    steps = _integrate_funding(series)  # [(dt_h, rate)]
    if not steps:
        return {}

    total_hours = sum(dt for dt, _ in steps)
    # 有利方向的年化毛资金费：net directional = |Σ rate×dt| / 总小时 × 每年小时数
    signed_total = sum(rate * dt for dt, rate in steps)  # 占名义比例·小时权重
    abs_total = abs(signed_total)
    gross_apr = (abs_total / total_hours) * HOURS_PER_YEAR if total_hours > 0 else 0.0

    # 成本：收费腿(HL) + 对冲腿(深场所,假设binance)各一次开+平 round-trip
    hl_fee = pr.get_offline_incentive(exchange).get("taker_rate", 0.00045)
    hedge_fee = pr.get_offline_incentive("binance").get("taker_rate", 0.0004)
    fee_drag = 2 * hl_fee + 2 * hedge_fee  # 占名义比例（一次性）

    # 滚动窗口：按累计小时切窗，窗内选净有利方向，扣一次性 fee_drag。
    window_hours = window_days * 24.0
    profitable_windows = 0
    total_windows = 0
    net_apr_samples: List[float] = []
    cur_hours = 0.0
    cur_signed = 0.0
    for dt, rate in steps:
        cur_hours += dt
        cur_signed += rate * dt
        if cur_hours >= window_hours:
            realized = abs(cur_signed)          # 持单向净有利实现资金费（占名义）
            net = realized - fee_drag
            total_windows += 1
            if net > 0:
                profitable_windows += 1
            net_apr_samples.append(net * (DAYS_PER_YEAR / window_days))
            cur_hours = 0.0
            cur_signed = 0.0

    daily_income = (abs_total / total_hours) * 24.0 if total_hours > 0 else 0.0
    breakeven_days = (fee_drag / daily_income) if daily_income > 1e-12 else None
    net_apr_median = statistics.median(net_apr_samples) if net_apr_samples else 0.0

    return {
        "symbol": symbol,
        "n_intervals": len(steps),
        "total_days": round(total_hours / 24.0, 1),
        "mean_hourly_rate": round(signed_total / total_hours, 8) if total_hours > 0 else 0.0,
        "gross_funding_apr": round(gross_apr, 4),
        "fee_drag": round(fee_drag, 6),
        "breakeven_days": round(breakeven_days, 2) if breakeven_days else None,
        "net_apr_median": round(net_apr_median, 4),
        "profitable_window_pct": round(profitable_windows / total_windows, 4) if total_windows else 0.0,
        "total_windows": total_windows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="hyperliquid")
    ap.add_argument("--symbols", type=int, default=15, help="回放 top-N symbol")
    ap.add_argument("--min-rows", type=int, default=500, help="symbol 最少历史行数")
    ap.add_argument("--notional", type=float, default=1000.0)
    ap.add_argument("--window-days", type=float, default=7.0)
    args = ap.parse_args()

    db = MarketSessionLocal()
    try:
        symbols = _top_symbols(db, args.exchange, args.symbols, args.min_rows)
        if not symbols:
            print(f"[Replay] {args.exchange} 无满足 min_rows={args.min_rows} 的 symbol")
            return

        print("=" * 92)
        print("资金费套利历史回放验证（Phase 4）")
        print("=" * 92)
        print(f"数据源: perp_funding / {args.exchange}（注意：本表仅单场所资金费，")
        print("        故验证的是【资金费捕获腿扣成本净EV】，非真正跨所价差——见脚本文档）")
        print(f"名义: ${args.notional:,.0f} | 窗口: {args.window_days} 天 | symbols: {len(symbols)}")
        print("-" * 92)
        print(f"{'symbol':<16}{'天数':>8}{'毛资金费APR':>12}"
              f"{'净APR中位':>11}{'保本天':>8}{'正EV窗口%':>10}")
        print("-" * 92)

        results = []
        for sym in symbols:
            series = _load_funding_series(db, args.exchange, sym)
            if len(series) < 10:
                continue
            r = validate_symbol(series, sym, args.exchange, args.notional, args.window_days)
            if not r:
                continue
            results.append(r)
            be = f"{r['breakeven_days']}" if r["breakeven_days"] is not None else "-"
            print(f"{r['symbol']:<16}{r['total_days']:>8}"
                  f"{r['gross_funding_apr']*100:>11.2f}%{r['net_apr_median']*100:>10.2f}%"
                  f"{be:>8}{r['profitable_window_pct']*100:>9.1f}%")

        print("-" * 92)
        if results:
            avg_gross = statistics.mean(r["gross_funding_apr"] for r in results)
            avg_net = statistics.mean(r["net_apr_median"] for r in results)
            avg_prof = statistics.mean(r["profitable_window_pct"] for r in results)
            positive = sum(1 for r in results if r["net_apr_median"] > 0)
            print(f"总体: 平均毛资金费 APR={avg_gross*100:.2f}%  平均净APR中位={avg_net*100:.2f}%  "
                  f"平均正EV窗口占比={avg_prof*100:.1f}%")
            print(f"      {positive}/{len(results)} 个 symbol 的净APR中位 > 0（收费腿可覆盖成本）")
            print()
            print("结论解读：")
            print("  · 净APR>0 的 symbol 说明【资金费收入】足以覆盖两腿开+平手续费，")
            print("    delta-neutral 载体在这些标的上具备正 EV 基础；跨所价差是额外 upside。")
            print("  · 净APR<0 的 symbol 资金费太薄、被手续费吃掉，不适合做资金费腿。")
            print("  · 局限：缺第二场所资金费历史，跨所价差与对冲腿资金费未纳入（保守低估）。")
        print("=" * 92)
    finally:
        db.close()


if __name__ == "__main__":
    main()
