"""h503：**趋势闸的现场复核**——顺势腿 vs 逆势腿，谁在赚钱？

背景（频率 vs 质量的正面冲突）：
  当前腿速 24–48/h，远低于 ≥60/h 硬约束。拦截画像显示主因是**趋势闸**：
    · `trend_pause_bp=15`：|5min 趋势| > 15bp 时封**逆势侧**（`trend_up` 110 次 + `trend_down` 21 次）；
    · `trend_only_q=0.35`：|5min 趋势| < 近期 |r300| 分布的 35 分位时封**加仓侧**（`trend_only_flat` 34 次）。
  这两条有历史证据（h323 markout：顺势腿 +4.4~+5.3bp、其余 90% −1.4~−2.9bp；
  h450/h451：|r300|≥30bp 段 +2.52bp, t=14.7, OOS 同号）。

  但"证据是几天前的、市场可能变了"。本脚本用**我们自己的近 N 小时成交**复核：
  对每一笔开仓/加仓腿，用**真实逐笔**在 **入场时刻（减 45s 延迟判定）** 算 5 分钟趋势
  `r300_bp`，然后按"腿方向是否与趋势同向"分组，比较已实现 net_bp。

  判读：
    · 顺势腿显著优于逆势腿 ⇒ 闸门**仍然正确**，频率地板在当前市况下**应当让步**
      （这是用户的取舍点，不是技术缺陷）；
    · 两组无差异甚至逆势更好 ⇒ 闸门**已过期**，应放宽（并在放宽后按 ≥60/h 判据试跑）。

口径注意：`ts` 是**检测时刻**，真实穿越早约 45s（h488 实测 p50=49s）⇒ 趋势按 ts−45s 取。
腿的分类沿用 h496：OPEN/ADD 计入"入场腿"，REDUCE/FLATTEN 不计。

用法：python scripts/h503_trend_gate_check.py [--hours 24]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h503_trend_gate.json"
LAG_S = 45


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


def trend_bp(cur, sym, t_ms, lookback_s=300):
    """真实逐笔 → (t−lookback, t] 的价格变化（bp，正=上涨）。"""
    cur.execute(
        "SELECT (ARRAY_AGG(price ORDER BY event_ts_ms ASC))[1]::float8, "
        "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8, count(*) "
        "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s",
        (sym + "USDT", t_ms - lookback_s * 1000, t_ms))
    r = cur.fetchone()
    if not r or not r[0] or not r[1] or int(r[2] or 0) < 2:
        return None
    p0, p1 = float(r[0]), float(r[1])
    return (p1 - p0) / p0 * 1e4 if p0 > 0 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), COALESCE(net_bp,0)::float8, "
                "COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    # 分类为入场腿（OPEN/ADD）
    entries = []
    for sym, rs in by_sym.items():
        cum = 0.0
        for ts, _s, side, qty, ep, net, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            is_entry = (ep == "" and (abs(before) < 1e-9 or (before > 0) == (signed > 0)))
            if is_entry:
                entries.append({"sym": sym, "ts": ts, "side": str(side).lower(),
                                "net": float(net or 0.0), "noti": float(noti or 0.0)})
    print(f"近 {a.hours:.0f}h：入场腿（OPEN/ADD）= {len(entries)}")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    aligned, against, notrend = [], [], []
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for e in entries:
                t_ms = int((e["ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                r3 = trend_bp(cur, e["sym"], t_ms)
                if r3 is None:
                    notrend.append(e)
                    continue
                e["r300"] = r3
                sgn = 1.0 if e["side"] == "buy" else -1.0
                (aligned if sgn * r3 > 0 else against).append(e)

    def rep(name, xs):
        if len(xs) < 3:
            print(f"  {name:10s} n={len(xs)}（样本不足）")
            return {}
        nets = [x["net"] for x in xs]
        m = st.mean(nets)
        sd = st.stdev(nets) if len(nets) > 1 else 0.0
        t = m / (sd / math.sqrt(len(nets))) if sd > 0 else 0.0
        r3 = [x["r300"] for x in xs]
        usd = sum(x["net"] * x["noti"] / 1e4 for x in xs)
        print(f"  {name:10s} n={len(xs):4d} 净/腿={m:+7.2f}bp (t={t:+5.2f}) "
              f"净额={usd:+7.2f}$ | 该组 |r300| 中位={st.median([abs(v) for v in r3]):.1f}bp")
        return {"n": len(xs), "net_bp": round(m, 3), "t": round(t, 2),
                "usd": round(usd, 3),
                "abs_r300_median": round(st.median([abs(v) for v in r3]), 2)}
    print("=" * 92)
    ra = rep("顺势腿", aligned)
    rg = rep("逆势腿", against)
    rn = rep("无趋势数据", notrend)
    print("=" * 92)
    # 按趋势强度分档（只对顺势腿）
    print("顺势腿按 |r300| 分档：")
    for lo, hi in ((0, 5), (5, 15), (15, 30), (30, 60), (60, 1e9)):
        xs = [x for x in aligned if lo <= abs(x["r300"]) < hi]
        if len(xs) >= 3:
            m = st.mean(x["net"] for x in xs)
            lab = f"{lo}-{hi}bp" if hi < 1e8 else f">={lo}bp"
            print(f"  |r300| {lab:>10s} n={len(xs):4d} 净/腿={m:+7.2f}bp")
    if ra and rg:
        diff = ra["net_bp"] - rg["net_bp"]
        if diff > 0.5 and ra["t"] > 1.0:
            verdict = (f"**闸门仍然正确**：顺势腿 {ra['net_bp']:+.2f}bp 显著优于逆势腿 "
                       f"{rg['net_bp']:+.2f}bp（差 {diff:+.2f}bp）⇒ 频率地板在当前市况下"
                       f"**应当让步**（这是取舍点，不是技术缺陷）")
        elif diff < -0.5:
            verdict = (f"**闸门已过期**：逆势腿 {rg['net_bp']:+.2f}bp 反而优于顺势腿 "
                       f"{ra['net_bp']:+.2f}bp ⇒ 应放宽趋势闸并试跑")
        else:
            verdict = (f"两组差异不显著（{diff:+.2f}bp）⇒ 趋势闸的收益不确定，"
                       f"可考虑放宽以恢复频率（按试跑纪律走）")
    else:
        verdict = "样本不足，无法裁决"
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "entries": len(entries), "aligned": ra, "against": rg,
         "no_trend": rn, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
