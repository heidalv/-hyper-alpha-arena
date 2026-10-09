# -*- coding: utf-8 -*-
"""H248 突发事件闸（F342）的上线观测：触发率 + 是否误伤成交。

# 上线配置

    sudden_move_bp            = 20      （校准值：≈P99.5，设计 18.3 次/天）
    sudden_move_k             = 1       （一个 tick ≈ 19s）
    sudden_move_cooldown_sec  = 0       （先只观测触发率，不加冷却）
    daily_loss_stop_pct       = 60      （临时放宽，避免锁死当天、观测中断）

# 要回答的三个问题

1. **触发率对不对**：`skip_counts.sudden_move` 的实测增速 vs 设计值 18.3 次/天？
2. **有没有误伤**：触发后是否"该成交却没成交"（对比 `cross_counts` 与 `fills` 的增速）？
3. **有没有把车道锁死**：`ok` 是否恒真、`lane_pause_counts` 是否异常、
   币的 `quote_bid/ask` 是否长期为 0？

⚠️ **本脚本只做观测，不改参数** —— 判据是"触发率达标 + 无锁死"，
然后才做"冷却 0 vs 120"的 A/B（那才是机制本身的 A/B）。

# 用法

    python scripts/h248_f342_watch.py            # 单次快照
    python scripts/h248_f342_watch.py --minutes 30   # 连续观测 N 分钟
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"


def snap() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def fills_since(t0) -> int:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FROM lane_ledger WHERE lane_id=%s AND ts >= %s",
                        (LANE, t0))
            return int(cur.fetchone()[0])


def report(t0, t_start) -> None:
    import datetime as dt
    j = snap()
    par = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        par.setdefault(k, v)
    sk = dict(j.get("skip_counts") or {})
    lp = dict(j.get("lane_pause_counts") or {})
    el = (time.time() - t_start) / 60.0
    sm = int(sk.get("sudden_move", 0))
    print(f"\n{'='*96}")
    print(f"  F342 观测  {dt.datetime.now():%H:%M:%S}　已观测 {el:.1f} 分钟")
    print(f"{'='*96}")
    print(f"  ok={j.get('ok')} reason={j.get('reason')!r} ticks={j.get('ticks')} "
          f"fills={j.get('fills')}")
    print(f"  sudden_move_bp={par.get('sudden_move_bp')} "
          f"k={par.get('sudden_move_k')} "
          f"cooldown={par.get('sudden_move_cooldown_sec')}")
    print(f"  daily_loss_stop_pct={par.get('daily_loss_stop_pct')} "
          f"day_pnl={j.get('day_pnl_usd')} / {j.get('day_pnl_limit_usd')}")
    print(f"\n  **skip_counts.sudden_move = {sm}**")
    if el > 0:
        per_day = sm / el * 60 * 24
        print(f"  ⇒ 实测触发率 = **{per_day:.1f} 次/天**（设计值 18.3）")
        if sm == 0 and el >= 10:
            print(f"     ⚠️ {el:.0f} 分钟内 0 次 ⇒ 要么行情平静，要么闸门没进代码路径")
            print(f"        排查：F342 需要重启才加载（已重启）；"
                  f"`sudden_move_bp>0` 才进判断")
    print(f"\n  skip_counts 全量 = {json.dumps(sk, ensure_ascii=False)}")
    print(f"  lane_pause_counts = {json.dumps(lp, ensure_ascii=False)}")
    print(f"\n  逐币挂单状态（判断有无长期不报价）：")
    for s, d in (j.get("states") or {}).items():
        b = d.get("quote_bid") or 0
        a = d.get("quote_ask") or 0
        q = d.get("qty") or 0
        flag = "  ⚠️ 两侧空" if (not b and not a) else ""
        print(f"    {s:8} qty={float(q):>14.4f}  bid={'有' if b else '无'}"
              f"  ask={'有' if a else '无'}{flag}")
    # 误伤检查：成交量是否塌
    try:
        n = fills_since(t0)
        print(f"\n  观测窗内的账本行数 = {n}"
              f"（{n/el:.1f}/分钟）" if el > 0 else "")
    except Exception as e:
        print(f"\n  账本查询失败：{e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--interval", type=float, default=120.0)
    a = ap.parse_args()

    import datetime as dt
    t_start = time.time()
    t0 = dt.datetime.now().astimezone()
    print("=" * 96)
    print("H248  F342 突发事件闸上线观测（只观测，不改参数）")
    print("=" * 96)
    print(f"  起点 {t0:%H:%M:%S}")
    if a.minutes <= 0:
        report(t0, t_start)
        return 0
    end = time.time() + a.minutes * 60
    while time.time() < end:
        report(t0, t_start)
        time.sleep(a.interval)
    report(t0, t_start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
