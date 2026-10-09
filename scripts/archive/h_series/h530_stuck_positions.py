"""h530：**卡住的持仓**体检——为什么持仓 4.2 小时没被超时/硬顶平掉？

现场（logs/mm_lane_status.json，2026-09-29 05:3xL）：
    ARB qty=-236.55  opened_age≈15079s
    NEAR qty=+12.30  opened_age≈15171s
    XRP  qty=-94.74  opened_age≈15062s
而 `max_one_side_seconds=90`（超时口径）与 `timeout_hard_taker_sec`（合规硬顶，
设计上 ≈300s）都远小于 4.2h ⇒ **持有时间超标 50 倍**。

为什么这件事重要（不是"看着别扭"）：
  1. 逐币敞口被长期占用 ⇒ `check_side_allowed` 的 `symbol_exposure` 持续拦截加仓侧，
     而频率审计里 `symbol_exposure` 正是 42/100 tick 的头号拦截之一
     ⇒ **直接压腿速（≥60/h 硬约束）**；
  2. `inv_ratio` 被系统性偏斜（引擎以为要减仓）⇒ 报价偏移、`spread_mult_reduce` 生效；
  3. 若 `timeout_hard_taker_sec` 缺失/为 0 而 `timeout_exit_maker_only=True`，
     则"被动出库"**没有任何时间兜底** ⇒ 仓位可以无限期挂着。

本脚本只读，输出：相关开关值、逐币卡住时长、引擎自己报的拦截计数、以及
"这条腿最后一次被判定成交是什么时候"（判断是"被动单一直没成交"还是"状态没被清"）。

用法：python scripts/h530_stuck_positions.py [--hours 6]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h530_stuck_positions.json"

SWITCHES = ["timeout_exit_maker_only", "timeout_hard_taker_sec",
            "max_one_side_seconds", "max_quote_age_sec", "ofi_flatten_maker_only",
            "take_profit_maker_grace_sec", "stop_maker_grace_sec",
            "max_net_directional_ratio", "max_leg_notional_mult"]


def read_env_dsn() -> str:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    now = dt.datetime.now().timestamp()
    params = d.get("params") or {}
    limits = d.get("limits") or {}
    print(f"worker ok={d.get('ok')} ticks={d.get('ticks')} "
          f"状态文件年龄={now - STATUS.stat().st_mtime:.0f}s")
    print("\n开关现值（注册表 params / 运行态 limits）")
    print("=" * 78)
    for k in SWITCHES:
        print(f"  {k:30s} params={params.get(k, '（未设置）')!s:>10s}   "
              f"limits={limits.get(k, '（缺）')!s:>10s}")
    st = d.get("states") or {}
    stuck = {s: v for s, v in st.items()
             if isinstance(v, dict) and abs(float(v.get("qty") or 0.0)) > 1e-12}
    print(f"\n卡住持仓：{len(stuck)} 个币")
    print("=" * 78)
    print(f"{'币':>6s} {'qty':>16s} {'持有s':>8s} {'超时倍数':>9s} "
          f"{'last_ts(判定)':>14s} {'状态里最后的 skip':>20s}")
    rows = []
    for s, v in sorted(stuck.items()):
        q = float(v.get("qty") or 0.0)
        op = float(v.get("opened_ts") or 0.0)
        age = (now - op) if op > 0 else 0.0
        last = float(v.get("last_ts") or 0.0)
        rows.append({"sym": s, "qty": q, "age_s": round(age, 1),
                     "last_ts_age_s": round(now - last, 1) if last else None,
                     "last_skip": v.get("skip") or v.get("last_skip")})
        print(f"{s:>6s} {q:+16.8f} {age:8.0f} {age/90.0:9.1f}x "
              f"{(now-last) if last else 0:14.0f} "
              f"{str(v.get('skip') or v.get('last_skip') or '-')[:20]:>20s}")
    print("\n引擎自报的拦截计数（skip_counts，前 12）")
    print("=" * 78)
    sc = d.get("skip_counts") or {}
    for k, n in sorted(sc.items(), key=lambda kv: -(kv[1] if isinstance(kv[1], (int, float)) else 0))[:12]:
        print(f"  {k:34s} {n}")
    for k in ("fills", "flattens", "quoted_decisions", "fills_per_hour"):
        if k in d:
            print(f"  [{k} = {d[k]}]")
    # 账本：这些币最后一次腿是什么（判断"被动单没成交" vs "状态没清"）
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, COALESCE(meta_json->>'exit_path','(入场腿)') AS p,
                       COALESCE(meta_json->>'exit_action',''), net_bp,
                       COALESCE(meta_json->>'flatten',''), COALESCE(meta_json->>'side','')
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
                  AND symbol = ANY(%s)
                ORDER BY ts DESC LIMIT 40""", (LANE, int(a.hours), list(stuck.keys())))
            legs = cur.fetchall()
    print(f"\n近 {a.hours:g}h 这三个币的**最近 40 条腿**（倒序）")
    print("=" * 78)
    for s, ts, p, act, nb, fl, side in legs:
        print(f"  {ts:%H:%M:%S} {s:>5s} {str(side):>4s} {p:<22s} "
              f"net={nb if nb is None else round(float(nb),2):>8} flatten={fl}")
    OUT.write_text(json.dumps({
        "switches": {k: {"params": params.get(k), "limits": limits.get(k)}
                     for k in SWITCHES},
        "stuck": rows, "skip_counts": sc,
        "recent_legs": [{"sym": s, "ts": str(ts), "path": p, "action": act,
                         "net_bp": (None if nb is None else float(nb)),
                         "flatten": fl, "side": side}
                        for s, ts, p, act, nb, fl, side in legs],
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
