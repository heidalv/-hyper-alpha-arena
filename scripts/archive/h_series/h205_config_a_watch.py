# -*- coding: utf-8 -*-
"""H205 配置 A 效果观察 —— 按 `exit_reason` 直接分类，不再反推。

# 配置 A 假设

改前的证据（当前时代逐腿，重置前）：

    段                        条数    净额$      avg net_bp
    A 止损侧（price_bp ≤−20）   74    −88.104     −35.53
    B 止盈侧（price_bp ≥+8）    38    +37.856     **+28.40**

**止盈是赚钱的**（+28.40bp/笔），但止损触发 74 次 vs 止盈 38 次，2:1 把利润吃光。

⇒ 假设：**止损线 25bp 让仓位在没回摆到 +12bp 止盈线之前就先撞线离场。**
   放宽到 80bp（并把 `max_one_side_seconds` 从 112 放到 7200，取消过早强平），
   让仓位有机会走到止盈那条路。

# 判据（这就是本脚本存在的意义）

| 观察 | 结论 |
|---|---|
| `stop_loss` 占比大幅下降、`take_profit` 上升 | **方向对** |
| flatten 主要来自 `timeout_maker_only` / `orphan_flatten` | **改错了地方**（止损不是主因） |
| flatten 数量大幅减少、净额转正 | 更强证据 |

# 为什么必须按 `exit_reason` 而不是按 `price_bp` 反推

反推能猜出"止损侧 / 止盈侧"，但**分不清"止损触发"与"超时强平"** ——
而这两者的修法完全相反（改阈值 vs 改超时）。
`exit_reason`（F335）直接把答案写在账本里。

# 用法

    python scripts/h205_config_a_watch.py            # 默认从配置 A 生效时刻起
    python scripts/h205_config_a_watch.py --since "2026-09-22 10:22:37"
    python scripts/h205_config_a_watch.py --compare  # 与配置 A 之前的同长度窗口对比
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
# 配置 A 生效 + 账户重置的同一时刻
DEFAULT_SINCE = "2026-09-22 10:22:37"


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


def window_stats(t0: datetime, t1: datetime) -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT
                  count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')),
                  count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')),
                  coalesce(sum(net_bp*notional/1e4) FILTER
                    (WHERE meta_json->>'flatten' NOT IN ('true','True')), 0),
                  coalesce(sum(net_bp*notional/1e4) FILTER
                    (WHERE meta_json->>'flatten' IN ('true','True')), 0),
                  coalesce(sum(net_bp*notional/1e4), 0)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s AND ts < %s
            """, (LANE, t0, t1))
            mk, fl, mku, flu, net = cur.fetchone()

            cur.execute("""
                SELECT coalesce(meta_json->>'exit_reason','(空)'),
                       count(*),
                       coalesce(sum(net_bp*notional/1e4), 0),
                       avg(price_bp)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s AND ts < %s
                  AND meta_json->>'flatten' IN ('true','True')
                GROUP BY 1 ORDER BY 2 DESC
            """, (LANE, t0, t1))
            reasons = cur.fetchall()

            cur.execute("""
                SELECT symbol,
                       count(*) FILTER (WHERE meta_json->>'flatten' NOT IN ('true','True')),
                       count(*) FILTER (WHERE meta_json->>'flatten' IN ('true','True')),
                       coalesce(sum(net_bp*notional/1e4), 0)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s AND ts < %s
                GROUP BY 1 ORDER BY 4
            """, (LANE, t0, t1))
            by_sym = cur.fetchall()
    return {"mk": int(mk or 0), "fl": int(fl or 0), "mku": float(mku or 0),
            "flu": float(flu or 0), "net": float(net or 0),
            "reasons": reasons, "by_sym": by_sym}


def show(label: str, st: dict, hours: float) -> None:
    print(f"  ── {label} ──")
    print(f"    maker {st['mk']} 腿 = {st['mku']:+.4f}    "
          f"flatten {st['fl']} 条 = {st['flu']:+.4f}    "
          f"**净额 {st['net']:+.4f}**")
    if hours > 0:
        print(f"    速率：{st['net']/hours:+.4f} $/h"
              f"   flatten {st['fl']/hours:.1f} 条/h"
              f"   maker {st['mk']/hours:.0f} 腿/h")
    if st["reasons"]:
        print(f"    {'出口原因':<24}{'条数':>6}{'净额$':>11}{'avg price_bp':>14}")
        for rs, n, usd, px in st["reasons"]:
            print(f"    {str(rs)[:23]:<24}{n:>6}{float(usd or 0):>+11.4f}"
                  f"{float(px or 0):>14.2f}")
    else:
        print("    （窗口内无 flatten）")
    if st["by_sym"]:
        print(f"    {'币':<9}{'maker':>7}{'flatten':>9}{'净额$':>11}")
        for s, mk, fl, net in st["by_sym"]:
            print(f"    {s:<9}{mk:>7}{fl:>9}{float(net or 0):>+11.4f}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--compare", action="store_true",
                    help="与生效前的同长度窗口对比")
    a = ap.parse_args()

    t0 = datetime.fromisoformat(a.since).astimezone()
    t1 = datetime.now().astimezone()
    hours = max(1e-9, (t1 - t0).total_seconds() / 3600.0)

    print("=" * 96)
    print("H205  配置 A 效果观察（按 exit_reason 直接分类）")
    print("=" * 96)
    print(f"  配置 A 生效 / 账户重置：{t0:%Y-%m-%d %H:%M:%S}")
    print(f"  现在：{t1:%Y-%m-%d %H:%M:%S}   已运行 {hours*60:.0f} 分钟")
    if hours < 1.0:
        print(f"  ⚠️ **样本不足 1 小时** ⇒ 只能看方向，不能下结论")
    print()

    show(f"配置 A（{hours*60:.0f} 分钟）", window_stats(t0, t1), hours)

    if a.compare:
        pre0 = t0 - timedelta(hours=hours)
        show(f"配置 A 之前（同长度 {hours*60:.0f} 分钟）",
             window_stats(pre0, t0), hours)

    print("  判据回顾：")
    print("    · 若 `stop_loss` 占比大幅下降、`take_profit` 上升 ⇒ 方向对")
    print("    · 若 flatten 主要来自 `timeout_maker_only` / `orphan_flatten`")
    print("      ⇒ 止损不是主因，改错了地方")
    print("    · ⚠️ `(空)` 表示该腿记录时 F335 还没上线（10:11:41 之前）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
