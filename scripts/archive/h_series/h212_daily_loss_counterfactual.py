# -*- coding: utf-8 -*-
"""H212 日亏闸离线反事实：把「早停」放在真实逐腿序列上算。

# 为什么不能直接跑 A/B

`lane_pause_reason` 的日亏闸是**当天锁存**的（`lane_day_pnl_usd` 读账本 +
`ts >= 当天0点`）⇒ 一旦触发就停到当天 24:00。所以：

- 每天只有**一次**独立观测；
- B 臂先跑会把当天后面的块全变成 0 成交 ⇒ A/B 交替在日初就自我污染；
- 要拿到 n 个配对样本得等 n 天。

但这个问题**不需要等**：它是账本上逐腿已实现盈亏的**顺序**问题，
全部输入都是历史数据。判据也很干净：

> 早停只对「触发点之后的那一段累计为负」的日子有价值。
> 若触发后的尾部是**正**的（当天早先的亏损后来赚回来了），早停就是把回撤固化。

本脚本对每一天、每一个阈值（权益的%）做这件事，并给出**触发后尾部的盈亏分布**。

# 口径

- 逐腿 = `lane_ledger` 按 `ts` 排序的 `net_bp * notional / 1e4`（美元）；
- 日 = 北京自然日（与 `daily_series` 的 `date_trunc('day', ts)` 同时区）；
- 权益：历史逐时权益快照不存在 ⇒ 用**当天起点权益**近似
  `equity_now - (cum_now - cum_at_day_start)`（已实现口径，忽略未实现与手续费差）；
  同时打印 `equity_now` 与每一天的推算起点，便于判断近似是否离谱；
- **时代裁剪**：`daily_loss_stop_pct` 的真实输入被 `ts >= stats_since` 裁剪
  （runner.py:319-324）。这里两种口径都出，标注哪个是真实生效的。

# 用法

    python scripts/h212_daily_loss_counterfactual.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h212_daily_loss_counterfactual.json"
PCTS = [5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 80.0]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def load(equity_now: float, days: int, since_epoch: float | None):
    """逐腿美元盈亏序列（含北京日与时代标记）。"""
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT ts,
                       (to_char(ts AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD')) AS bj_day,
                       coalesce(net_bp,0)*coalesce(notional,0)/1e4 AS usd,
                       coalesce(notional,0) AS notional
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                ORDER BY ts ASC
            """, (LANE, str(int(days))))
            rows = cur.fetchall()
    if not rows:
        return [], []
    # 时代裁剪：闸门真实输入只算 stats_since 之后
    eras = [r for r in rows if since_epoch is None or r[0].timestamp() >= since_epoch]
    return rows, eras


def day_slices(rows):
    """{北京日: [(ts, usd, notional), ...]}（已按 ts 升序）。"""
    d = {}
    for ts, day, usd, notional in rows:
        d.setdefault(day, []).append((ts, float(usd), float(notional)))
    return d


def walk(legs, equity0: float, pct: float):
    """在一天内按顺序走，返回 (触发腿序号, 触发时刻, 触发前累计, 触发后累计)。

    触发条件与 `lane_pause_reason` 逐字一致：`cum <= -equity0*pct/100`。
    """
    thr = -abs(equity0 * pct / 100.0)
    cum = 0.0
    for i, (ts, usd, _n) in enumerate(legs):
        cum += usd
        if cum <= thr:
            after = sum(x[1] for x in legs[i + 1:])
            return i, ts, cum, after
    return None, None, cum, 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--equity", type=float, default=0.0,
                    help="当前权益（默认从状态文件读）")
    a = ap.parse_args()

    eq = a.equity
    if eq <= 0:
        try:
            j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
            eq = float(j.get("equity") or 0.0)
        except Exception:
            eq = 0.0
    if eq <= 0:
        eq = 300.0
    since = None
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        s = j.get("stats_since")
        if s:
            since = datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        pass

    rows, eras = load(eq, a.days, since)
    if not rows:
        print("账本无数据")
        return 1

    total = sum(r[2] for r in rows)
    days_all = day_slices(rows)
    print("=" * 100)
    print("H212  日亏闸离线反事实（早停到底是在止损还是在固化回撤）")
    print("=" * 100)
    print(f"  当前权益（读状态文件）= ${eq:.2f}")
    print(f"  窗口 = 最近 {a.days} 天，共 {len(rows)} 腿")
    print(f"  已实现合计 = ${total:+.2f}")
    print(f"  推算第 1 天起点权益 ≈ ${eq - total:.2f}（已实现口径近似）")
    print(f"  时代起点 stats_since = "
          f"{datetime.fromtimestamp(since).astimezone():%Y-%m-%d %H:%M:%S}"
          if since else "  时代起点 = （无）")
    print(f"  时代内腿数 = {len(eras)}")

    for label, sub in (("全窗口（不裁时代）", rows), ("时代内（真实闸门口径）", eras)):
        ds = day_slices(sub)
        if not ds:
            continue
        print(f"\n{'━'*100}\n  {label}：{len(ds)} 个北京日\n{'━'*100}")
        # 逐日推算起点权益：eq_now - (合计 - 当天起点前的累计)
        cum = 0.0
        day_info = []
        for day in sorted(ds):
            legs = ds[day]
            eq0 = eq - (total - cum)
            daynet = sum(x[1] for x in legs)
            cum += daynet
            day_info.append((day, legs, eq0, daynet))

        print(f"  {'日期':<12}{'腿数':>6}{'日净$':>11}{'起点权益':>11} | "
              + "".join(f"{'≤'+str(int(p))+'%':>10}" for p in PCTS))
        print("  " + "-" * 96)
        mat = {}
        for day, legs, eq0, daynet in day_info:
            cells = []
            for p in PCTS:
                i, ts, cum_at, after = walk(legs, eq0, p)
                mat[(day, p)] = (i, after)
                cells.append(f"{after:>+10.2f}" if i is not None else f"{'—':>10}")
            print(f"  {day:<12}{len(legs):>6}{daynet:>+11.2f}{eq0:>11.2f} | "
                  + "".join(cells))

        print(f"\n  上表数字 = **触发后当天剩余腿的累计盈亏**（'—' = 当天从未触发）")
        print(f"  判读：负 = 早停避免了这么多亏损（有效）；正 = 早停把赚回来的钱锁死了（有害）")
        for p in PCTS:
            vals = [mat[(d, p)][1] for d, _l, _e, _n in day_info
                    if mat[(d, p)][0] is not None]
            fired = len(vals)
            if not fired:
                print(f"\n  ≤{p:>4.0f}%：**{len(day_info)} 天里 0 天触发** ⇒ 完全无作用")
                continue
            npos = sum(1 for v in vals if v > 0)
            print(f"\n  ≤{p:>4.0f}%：{fired}/{len(day_info)} 天触发　"
                  f"触发后尾部合计 {sum(vals):+.2f}　中位 {st.median(vals):+.2f}　"
                  f"最差 {min(vals):+.2f}　最好 {max(vals):+.2f}　"
                  f"为正的天数 {npos}/{fired}")
            if sum(vals) < 0 and npos <= fired / 2:
                print(f"          ⇒ **早停有效**（多数触发的日子尾部继续亏）")
            elif sum(vals) > 0:
                print(f"          ⇒ **早停有害**（尾部合计为正，即亏损被赚回来了）")
            else:
                print(f"          ⇒ **不显著**（正负混杂，不能只凭净额下结论）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "equity_now": eq, "days": a.days, "legs": len(rows),
        "realized_total": total, "pcts": PCTS,
        "note": "触发后尾部 = 触发之后当天剩余腿的累计已实现盈亏（美元）",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
