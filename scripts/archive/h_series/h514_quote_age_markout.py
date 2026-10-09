"""h514：**挂单年龄 vs 填单质量**——"陈旧挂单"是不是逆向选择的来源？

假设（有现成参数，可直接处置）：
  引擎判定成交的时间点比真实穿越晚 ≈45s（h488 实测 p50=49s），且成交价记作
  **当时的挂单价** ⇒ 若一笔成交发生在**挂出很久之后**，那个价格锚定的是**旧的中间价**，
  逆向选择应当更严重。
  若成立 ⇒ 有现成参数可用：`max_quote_age_sec`（默认 90s，**超过即丢弃挂单不做成交判定**）
  可以下调（例如 30s），直接把"陈旧挂单成交"这一类砍掉。

口径（每笔入场腿，48h）：
  · `quote_age = ts_fill − quote_ts`（账本 `meta_json.quote_ts` 覆盖率 100%）；
  · `markout60` = 有向 `(mid_{t+60} − fill_px)`（含捕获）；
  · 分档：<15 / 15-30 / 30-45 / 45-60 / 60-90 / >90 秒。

判读：
  · markout 随年龄单调恶化 ⇒ **下调 `max_quote_age_sec` 是低风险高收益的改动**
    （只砍掉最毒的那批成交，不动其它逻辑）；
  · 无单调关系 ⇒ 陈旧挂单不是问题，别再往这个方向想。

用法：python scripts/h514_quote_age_markout.py [--hours 48]
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
OUT = ROOT / "research_l1" / "out" / "h514_quote_age.json"
LAG_S = 45
BINS = ((0, 15), (15, 30), (30, 45), (45, 60), (60, 90), (90, 1e9))


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


def mid_at(cur, sym, t_ms, tol=20000):
    cur.execute(
        "SELECT (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]::float8 "
        "FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s "
        "AND event_ts_ms <= %s AND bid_px>0 AND ask_px>bid_px",
        (sym + "USDT", t_ms - tol, t_ms + tol))
    r = cur.fetchone()
    return float(r[0]) if r and r[0] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8, "
                "COALESCE((meta_json->>'quote_ts')::float8,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, syms, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    ents = []
    for sym, rs in by_sym.items():
        cum = 0.0
        for ts, _s, side, qty, ep, px, qts in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            if ep == "" and (abs(before) < 1e-9 or (before > 0) == (signed > 0)) and px > 0:
                ents.append({"sym": sym, "ts": ts, "side": str(side).lower(),
                             "px": float(px), "qts": float(qts or 0.0)})
    print(f"近 {a.hours:.0f}h 入场腿 {len(ents)}")
    have = [e for e in ents if e["qts"] > 0]
    print(f"  其中带 `quote_ts` 的 {len(have)}（{100*len(have)/max(len(ents),1):.0f}%）")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    grid: dict = collections.defaultdict(lambda: {"mo": [], "n": 0})
    ages = []
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for e in have:
                age = e["ts"].timestamp() - e["qts"]
                if age < 0 or age > 3600:
                    continue
                t_ms = int((e["ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                m60 = mid_at(cur, e["sym"], t_ms + 60000)
                if m60 is None:
                    continue
                sgn = 1.0 if e["side"] == "buy" else -1.0
                mo = sgn * (m60 - e["px"]) / e["px"] * 1e4
                b = next((f"{lo}-{hi}" if hi < 1e8 else f">={lo}"
                          for lo, hi in BINS if lo <= age < hi), None)
                if b is None:
                    continue
                grid[b]["mo"].append(mo)
                grid[b]["n"] += 1
                ages.append(age)
    if not ages:
        print("无可用样本")
        return 1
    print(f"挂单年龄：中位 {st.median(ages):.0f}s  p90 "
          f"{sorted(ages)[int(.9*(len(ages)-1))]:.0f}s  max {max(ages):.0f}s")
    print("=" * 92)
    print(f"{'挂单年龄(s)':>12s} {'腿数':>6s} {'占比':>7s} {'markout60':>11s} {'t':>7s}")
    tot = sum(g["n"] for g in grid.values())
    res = {}
    for lo, hi in BINS:
        b = f"{lo}-{hi}" if hi < 1e8 else f">={lo}"
        g = grid.get(b)
        if not g or g["n"] < 5:
            continue
        m = st.mean(g["mo"])
        sd = st.stdev(g["mo"]) if len(g["mo"]) > 1 else 0.0
        t = m / (sd / math.sqrt(len(g["mo"]))) if sd > 0 else 0.0
        print(f"{b:>12s} {g['n']:6d} {100.0*g['n']/tot:6.1f}% {m:+11.2f} {t:+7.2f}")
        res[b] = {"n": g["n"], "mo": round(m, 3), "t": round(t, 2)}
    print("=" * 92)
    ks = list(res)
    if len(ks) >= 3:
        young = [res[k]["mo"] for k in ks[:2]]
        old = [res[k]["mo"] for k in ks[-2:]]
        dy, do = st.mean(young), st.mean(old)
        mono = all(res[ks[i]]["mo"] >= res[ks[i + 1]]["mo"] - 0.15
                   for i in range(len(ks) - 1))
        print(f"年轻档（前两档）均值 {dy:+.2f}bp  vs  陈旧档（后两档）均值 {do:+.2f}bp "
              f"⇒ Δ={do-dy:+.2f}bp；单调恶化={mono}")
        if do - dy < -0.5:
            verdict = (f"**陈旧挂单显著更毒**（Δ={do-dy:+.2f}bp）⇒ 下调 "
                       f"`max_quote_age_sec`（现 90s）到 ~30–45s 是低风险高收益改动："
                       f"只砍掉最毒的那批成交。需按试跑纪律走（预注册 + 判定）")
        elif do - dy > 0.5:
            verdict = (f"陈旧挂单反而**更好**（Δ={do-dy:+.2f}bp）⇒ 与假设相反，"
                       f"不应下调 `max_quote_age_sec`")
        else:
            verdict = (f"挂单年龄与填单质量**无实质关系**（Δ={do-dy:+.2f}bp）"
                       f"⇒ 陈旧挂单不是逆向选择的来源，此方向关闭")
    else:
        verdict = "样本不足"
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps({"hours": a.hours, "entries": len(have),
                               "age_median": round(st.median(ages), 1),
                               "bins": res, "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
