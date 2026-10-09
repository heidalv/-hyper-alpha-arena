"""h497：**主动平仓（FLATTEN）的按路径美元分解**——亏损到底出自哪条出口。

上一轮（h496）的修正资金地图给出结论：26% 的腿（引擎主动平仓）吃掉 **93%** 的亏损
（−13.45$ / −14.43$，近 12h）。但 FLATTEN 内部还有 7 条出口
（ofi_flatten / 硬顶 / 止损 / 反转衰减 / 尾随锁利 / 止盈），修法完全不同
⇒ 必须**按路径拆到美元**才知道下一刀砍哪里。

口径：腿的分类用 `h494/h496` 的**累计持仓**法（不看 `exit_path` 判方向），
但 FLATTEN 行本身带 `exit_path`，直接按它分组即可。
同时给出 **h472 部署前/后**两个子窗口的对照（部署 2026-09-29 02:33:15+08）。

用法：python scripts/h497_flatten_breakdown.py [--hours 12]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h497_flatten_breakdown.json"
H472 = "2026-09-29 02:33:15+08"


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


def report(rows, label):
    g: dict = collections.defaultdict(lambda: {"n": 0, "net": [], "fee": [], "sp": [],
                                               "px": [], "usd": 0.0, "notional": 0.0})
    for ep, net, fee, sp, px, noti in rows:
        d = g[ep or "(无路径)"]
        d["n"] += 1
        d["net"].append(net)
        d["fee"].append(fee)
        d["sp"].append(sp)
        d["px"].append(px)
        d["usd"] += net * noti / 1e4
        d["notional"] += noti
    tot_usd = sum(d["usd"] for d in g.values())
    print(f"\n── {label}（主动平仓腿 {len(rows)}，净额 {tot_usd:+.2f}$）──")
    print(f"{'exit_path':>24s} {'腿数':>5s} {'净bp/腿':>8s} {'费bp/腿':>8s} "
          f"{'价差bp':>8s} {'名义$':>9s} {'净额$':>9s} {'占亏损':>8s}")
    for ep, d in sorted(g.items(), key=lambda kv: kv[1]["usd"]):
        m = lambda v: st.mean(v) if v else 0.0        # noqa: E731
        print(f"{ep:>24s} {d['n']:5d} {m(d['net']):8.2f} {m(d['fee']):8.2f} "
              f"{m(d['sp']):8.2f} {d['notional']:9.0f} {d['usd']:9.2f} "
              f"{100.0*d['usd']/tot_usd if tot_usd else 0:7.1f}%")
    return {k: {"n": v["n"], "net_bp": round(st.mean(v["net"]), 3) if v["net"] else None,
                "fee_bp": round(st.mean(v["fee"]), 3) if v["fee"] else None,
                "net_usd": round(v["usd"], 3),
                "notional_usd": round(v["notional"], 1)} for k, v in g.items()}, tot_usd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--since", default="",
                    help="起始时刻（ISO，本地/带时区）；给定时**只用这个下界**，"
                         "用于排除已回滚机制的历史腿（如 jump_exit 只在 09-28 11:37 前存在）")
    a = ap.parse_args()
    if a.since:
        t0 = dt.datetime.fromisoformat(a.since)
        _w = "ts > %s::timestamptz"
        _args = (LANE, t0)
    else:
        _w = "ts > now() - make_interval(hours => %s::int)"
        _args = (LANE, int(a.hours))
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, COALESCE(meta_json->>'exit_path',''), "
                "COALESCE(net_bp,0)::float8, COALESCE(fee_bp,0)::float8, "
                "COALESCE(spread_bp,0)::float8, COALESCE(price_bp,0)::float8, "
                "COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND meta_json->>'exit_path' IS NOT NULL "
                "AND COALESCE(meta_json->>'exit_path','') <> '' "
                f"AND {_w} ORDER BY ts", _args)
            rows = cur.fetchall()
    allr = [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]
    pre = [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows
           if r[0] < dt.datetime.fromisoformat(H472)]
    post = [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows
            if r[0] >= dt.datetime.fromisoformat(H472)]
    print(f"近 {a.hours:.0f}h 主动平仓腿 {len(allr)}（h472 前 {len(pre)} / 后 {len(post)}）")
    tab_all, tot_all = report(allr, "全窗口")
    tab_pre, tot_pre = report(pre, "h472 之前")
    tab_post, tot_post = report(post, "h472 之后")
    print("\n" + "=" * 96)
    # 每小时口径（便于与"亏损/小时"对齐）
    hr = a.hours
    print(f"折算：全窗口 {tot_all:+.2f}$ / {hr:.0f}h = {tot_all/hr:+.3f}$/h")
    if post:
        hrs_post = (dt.datetime.now(dt.timezone.utc).astimezone()
                    - dt.datetime.fromisoformat(H472)).total_seconds() / 3600.0
        print(f"h472 后：{tot_post:+.2f}$ / {hrs_post:.1f}h = {tot_post/max(hrs_post,1e-9):+.3f}$/h")
    worst = min(tab_all.items(), key=lambda kv: kv[1]["net_usd"])
    print(f"\n单条最大失血路径（全窗口）: **{worst[0]}** "
          f"{worst[1]['n']} 腿 {worst[1]['net_usd']:+.2f}$ "
          f"（{worst[1]['net_bp']:+.2f}bp/腿，费 {worst[1]['fee_bp']:+.2f}bp/腿）")
    OUT.write_text(json.dumps(
        {"hours": a.hours, "n_flatten": len(allr),
         "all": tab_all, "pre_h472": tab_pre, "post_h472": tab_post,
         "usd_per_hour_all": round(tot_all / hr, 4),
         "worst_path": worst[0]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
