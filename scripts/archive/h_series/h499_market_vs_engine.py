"""h499：**市场活跃度 vs 引擎腿速**——判定腿速下滑是市场还是引擎。

为什么急：h472（ofi_flatten 改被动）之后腿速从 78/h(前) → 69.7/h(84min) →
**49/h(60min) → 36/h(20min)**，已低于用户硬约束 ≥60 腿/h。
必须立刻分清：
  · **市场安静** ⇒ 无可作为（不该为凑腿数而交易）；
  · **引擎自锁**（被动出场等待期封住加仓侧 ⇒ 新仓开不出来）⇒ 必须干预
    （提前回滚 h472，或改成"被动优先 + 宽限 N 秒后 taker"）。

口径（只读）：用**真实逐笔** `asterdex_trades` 作为市场活跃度基准
（与引擎无关），逐 10 分钟对照 `lane_ledger` 腿数；两者都按**在役币**过滤。

用法：python scripts/h499_market_vs_engine.py [--hours 3]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h499_market_vs_engine.json"
H472 = dt.datetime.fromisoformat("2026-09-29 02:33:15+08")


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
    ap.add_argument("--hours", type=float, default=3.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
    print("在役币:", syms)
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            cur.execute(
                "SELECT to_timestamp(floor((event_ts_ms/1000.0)/600)*600) AS b, "
                "count(*), COALESCE(sum(qty*price),0)::float8, count(DISTINCT symbol) "
                "FROM asterdex_trades WHERE symbol = ANY(%s) "
                "AND event_ts_ms > (extract(epoch from now())*1000 - %s) "
                "GROUP BY 1 ORDER BY 1", ([s + "USDT" for s in syms], int(a.hours * 3600e3)))
            mkt = cur.fetchall()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT to_timestamp(floor(extract(epoch from ts)/600)*600) AS b, "
                "count(*), count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')='') "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) "
                "GROUP BY 1 ORDER BY 1", (LANE, syms, int(a.hours)))
            eng = cur.fetchall()
    e = {int(b.timestamp()): (int(n), int(ne)) for b, n, ne in eng}
    print("=" * 96)
    print(f"{'10min 桶(本地)':>16s} {'市场成交笔':>10s} {'市场名义$':>11s} "
          f"{'引擎腿':>7s} {'入场腿':>7s} {'腿/市场笔':>10s} {'阶段':>9s}")
    rows = []
    for b, n, notional, nsym in mkt:
        k = int(b.timestamp())
        en, ene = e.get(k, (0, 0))
        phase = "post-h472" if b.replace(tzinfo=dt.timezone(dt.timedelta(hours=8))) >= H472 \
            else "pre-h472"
        ratio = (en / n) if n else float("nan")
        print(f"{b.astimezone():%m-%d %H:%M} {int(n):10d} {float(notional):11.0f} "
              f"{en:7d} {ene:7d} {ratio:10.4f} {phase:>9s}")
        rows.append({"ts": b.isoformat(), "mkt_trades": int(n),
                     "mkt_notional": round(float(notional), 1),
                     "engine_legs": en, "engine_entries": ene,
                     "legs_per_mkt_trade": round(ratio, 4) if n else None,
                     "phase": phase})
    # 汇总对比
    import statistics as st
    pre = [r for r in rows if r["phase"] == "pre-h472" and r["mkt_trades"] > 0]
    post = [r for r in rows if r["phase"] == "post-h472" and r["mkt_trades"] > 0]
    print("=" * 96)
    for name, grp in (("pre-h472 ", pre), ("post-h472", post)):
        if not grp:
            continue
        mt = st.mean(r["mkt_trades"] for r in grp)
        ml = st.mean(r["engine_legs"] for r in grp)
        ra = st.mean(r["legs_per_mkt_trade"] for r in grp if r["legs_per_mkt_trade"])
        print(f"{name}: 桶数 {len(grp):3d} | 市场笔/10min 均值 {mt:7.0f} | "
              f"引擎腿/10min 均值 {ml:6.1f} | 腿/市场笔 {ra:.4f}")
    if pre and post:
        mt0 = st.mean(r["mkt_trades"] for r in pre)
        mt1 = st.mean(r["mkt_trades"] for r in post)
        ml0 = st.mean(r["engine_legs"] for r in pre)
        ml1 = st.mean(r["engine_legs"] for r in post)
        r0 = ml0 / mt0 if mt0 else float("nan")
        r1 = ml1 / mt1 if mt1 else float("nan")
        print(f"\n市场活跃度变化 = {mt1/mt0 if mt0 else float('nan'):.2f}×；"
              f"引擎腿数变化 = {ml1/ml0 if ml0 else float('nan'):.2f}×；"
              f"单位市场活跃度的腿产出 = {r0:.4f} → {r1:.4f} "
              f"（{r1/r0 if r0 else float('nan'):.2f}×）")
        if r0 and r1 / r0 < 0.75 and mt1 / mt0 > 0.85:
            verdict = ("⇒ **引擎自锁**：市场活跃度基本没降，但单位活跃度的腿产出掉了 "
                       f"{100*(1-r1/r0):.0f}% ⇒ 是引擎侧（被动出场等待期封住加仓侧）"
                       "⇒ 应干预：把 h472 提前回滚，或改成「被动优先 + 宽限 N 秒后 taker」")
        elif mt1 / mt0 <= 0.85:
            verdict = ("⇒ **市场安静**：市场成交笔数本身降了 "
                       f"{100*(1-mt1/mt0):.0f}% ⇒ 腿速下滑主要是市场原因，"
                       "不该为凑腿数而放宽闸门（放宽只会提高亏损腿的比例）")
        else:
            verdict = ("⇒ 两者都在降/不显著 ⇒ 需更长窗口再判（或查闸门拦截率变化）")
        print("\n" + verdict)
        OUT.write_text(json.dumps({"rows": rows, "verdict": verdict,
                                   "mkt_mean_pre": mt0, "mkt_mean_post": mt1,
                                   "legs_mean_pre": ml0, "legs_mean_post": ml1,
                                   "yield_pre": r0, "yield_post": r1},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
