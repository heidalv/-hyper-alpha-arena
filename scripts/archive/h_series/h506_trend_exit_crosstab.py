"""h506：**趋势强度 × 出场路径** 交叉表——强趋势里往返的钱从哪条出口漏掉。

动机：h504 发现"趋势越强、往返越差"（|r300| 0-10bp: −0.00 → 20-40bp: −2.75 →
≥40bp: −4.69，t=−3.01），并把机制解释为"强趋势里均值回归式出场失效"。
但那只是解释，尚未定位到**具体出口**。本脚本把"往返按入场 |r300| 分档"
与"收口方式（h496 口径：REDUCE 被动 / stop_loss / timeout_hard / ofi_flatten /
reversal_decay / trail_lock / take_profit）"交叉，回答：

  Q1 强趋势档的往返主要由哪条出口收口？各自贡献多少美元？
  Q2 弱趋势档呢？（若弱趋势靠 REDUCE 被动收口、强趋势靠 stop/硬顶，
      则"出场机制与趋势不匹配"成立，且可量化要改哪一条。）

口径：往返用 h494 累计归零重建；分类与 h496 一致；|r300| 用真实逐笔在
"首腿时刻 −45s"（h488 的延迟校正）计算。

用法：python scripts/h506_trend_exit_crosstab.py [--hours 72]
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
OUT = ROOT / "research_l1" / "out" / "h506_trend_exit.json"
LAG_S = 45
BUCKETS = ((0, 10), (10, 20), (20, 40), (40, 1e9))


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
    cur.execute(
        "SELECT (ARRAY_AGG(price ORDER BY event_ts_ms ASC))[1]::float8, "
        "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8, count(*) "
        "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s",
        (sym + "USDT", t_ms - lookback_s * 1000, t_ms))
    r = cur.fetchone()
    if not r or not r[0] or not r[1] or int(r[2] or 0) < 2:
        return None
    return (float(r[1]) - float(r[0])) / float(r[0]) * 1e4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
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
    trips = []
    for sym, rs in by_sym.items():
        cum = 0.0
        peak = 0.0
        t = None
        for ts, _s, side, qty, ep, net, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            cum += signed
            peak = max(peak, abs(cum))
            if t is None:
                t = {"sym": sym, "open_ts": ts, "first_side": str(side).lower(),
                     "usd": 0.0, "noti": 0.0, "paths": collections.Counter()}
            t["usd"] += float(net or 0.0) * float(noti or 0.0) / 1e4
            t["noti"] += float(noti or 0.0)
            if ep:
                t["paths"][ep] += 1
            if abs(cum) <= max(1e-6, 1e-4 * max(peak, 1e-9)):
                t["bp"] = (t["usd"] / t["noti"] * 1e4) if t["noti"] else 0.0
                t["closed_by"] = (t["paths"].most_common(1)[0][0] if t["paths"]
                                  else "REDUCE")
                trips.append(t)
                t = None
                peak = 0.0
    print(f"近 {a.hours:.0f}h：往返 {len(trips)}")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    grid: dict = collections.defaultdict(
        lambda: {"n": 0, "usd": 0.0, "bp": [], "paths": collections.Counter(),
                 "path_usd": collections.Counter()})
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for t in trips:
                t_ms = int((t["open_ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                r3 = trend_bp(cur, t["sym"], t_ms)
                if r3 is None:
                    continue
                ab = abs(r3)
                bucket = next(f"{lo}-{hi}" if hi < 1e8 else f">={lo}"
                              for lo, hi in BUCKETS if lo <= ab < hi)
                g = grid[bucket]
                g["n"] += 1
                g["usd"] += t["usd"]
                g["bp"].append(t["bp"])
                g["paths"][t["closed_by"]] += 1
                g["path_usd"][t["closed_by"]] += t["usd"]
    print("=" * 104)
    def _lo(b):
        s = b.split("-")[0]
        return float(s.replace(">=", "").replace("+", "").strip() or 0)
    for bucket in sorted(grid, key=_lo):
        g = grid[bucket]
        print(f"\n── |r300| {bucket} bp（n={g['n']}，合计 {g['usd']:+.2f}$，"
              f"往返均值 {st.mean(g['bp']):+.2f}bp）──")
        print(f"{'收口方式':>24s} {'次数':>6s} {'占比':>7s} {'该方式净额$':>12s} "
              f"{'每往返$':>10s}")
        for path, cnt in g["paths"].most_common():
            usd = g["path_usd"][path]
            print(f"{path:>24s} {cnt:6d} {100.0*cnt/g['n']:6.1f}% {usd:12.2f} "
                  f"{usd/cnt:10.3f}")
    # 结论：强趋势档里 stop/硬顶 的占比与贡献
    strong = {k: v for k, v in grid.items() if k.startswith("40") or k.startswith(">=")}
    weak = {k: v for k, v in grid.items() if k.startswith("0-")}
    def agg(d, name):
        n = sum(g["n"] for g in d.values())
        usd = sum(g["usd"] for g in d.values())
        p = collections.Counter()
        pu = collections.Counter()
        for g in d.values():
            p.update(g["paths"])
            pu.update(g["path_usd"])
        return {"n": n, "usd": round(usd, 2),
                "paths": dict(p), "path_usd": {k: round(v, 2) for k, v in pu.items()},
                "stop_share": round(p.get("stop_loss_taker", 0) / max(n, 1), 3),
                "reduce_share": round(p.get("REDUCE", 0) / max(n, 1), 3),
                "hard_share": round(p.get("timeout_hard_taker", 0) / max(n, 1), 3)}
    s_ = agg(strong, "强趋势") if strong else {}
    w_ = agg(weak, "弱趋势") if weak else {}
    print("=" * 104)
    if s_ and w_:
        print(f"强趋势档（≥40bp）：n={s_['n']} 净额={s_['usd']:+.2f}$ | "
              f"止损占比 {100*s_['stop_share']:.0f}% 被动收口 {100*s_['reduce_share']:.0f}% "
              f"硬顶 {100*s_['hard_share']:.0f}%")
        print(f"弱趋势档（0-10bp）：n={w_['n']} 净额={w_['usd']:+.2f}$ | "
              f"止损占比 {100*w_['stop_share']:.0f}% 被动收口 {100*w_['reduce_share']:.0f}% "
              f"硬顶 {100*w_['hard_share']:.0f}%")
        verdict = (f"强趋势里**止损占比 {100*s_['stop_share']:.0f}%（弱趋势 {100*w_['stop_share']:.0f}%）**"
                   f"、被动收口 {100*s_['reduce_share']:.0f}%（弱趋势 {100*w_['reduce_share']:.0f}%）"
                   f"⇒ " + ("「出场机制与趋势不匹配」成立：强趋势里被动回摆收口显著更少、"
                            "止损显著更多 ⇒ 应针对**强趋势**单独设计出口（顺趋势出场/延迟止损）"
                            if s_["stop_share"] > 1.5 * max(w_["stop_share"], 1e-9)
                            else "两侧出口构成接近 ⇒ 亏损差异不是出口构成造成的，"
                                 "更可能在**入场定价**（与 h470 的结论一致）"))
    else:
        verdict = "样本不足，无法给交叉结论"
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "trips": len(trips),
         "grid": {k: {"n": v["n"], "usd": round(v["usd"], 2),
                      "bp_mean": round(st.mean(v["bp"]), 3),
                      "paths": dict(v["paths"]),
                      "path_usd": {kk: round(vv, 2) for kk, vv in v["path_usd"].items()}}
                  for k, v in grid.items()},
         "strong": s_, "weak": w_, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
