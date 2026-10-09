"""h518：**固定 40bp 止损对高波动币是否太紧**——把 h470 的反事实按波动分组。

背景：
  · 止损率差异极大：BNB 2.0%、XRP 7.7%、ARB 15.7%、NEAR 24.7%、ENA 29.4%；
  · 而这些币的 15s 噪声同样差 4–5 倍：BNB 2.7bp、XRP 4.8、NEAR 11.7、ENA 12.8；
  · `stop_loss_bp=40` 是**全币同一常数** ⇒ 对高噪声币，40bp 可能只是"几倍噪声"，
    会被噪声本身打穿（而不是被信息打穿）。
  · h470 已证：止损与"不止损、等 300s 被动出场"整体 EV 相当（Δ=−0.55bp, t=−0.08）
    —— 但那是**全样本平均**；本脚本按"币的噪声水平"分组重做，看这个中性结论
    在不同噪声档是否同样成立（若高噪声档"持有更好"，就应做**波动自适应止损**）。

口径（与 h470 完全一致，便于对照）：
  对每个止损出场腿：`net_alt = net_bp − d_post300 + |fee_bp|`
  （d_post300 = 出场后 300s 的**离场方向有利漂移**；持有到 +300s、按中价被动出场）。
  分组：按该币 15s 噪声中位分档（低/中/高）。

判读：
  · 高噪声档 `net_alt − net_bp` 显著 >0 ⇒ **止损对高噪声币是错的** ⇒ 做波动自适应
    （或按币设阈值）；
  · 各档都 ≈0 ⇒ h470 的中性结论稳健，**别动止损**（把精力留给入场侧）。

用法：python scripts/h518_stop_cf_by_vol.py [--hours 72]
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
OUT = ROOT / "research_l1" / "out" / "h518_stop_cf_vol.json"


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


def mid_at(cur, sym, t_sec, tol_ms=6000):
    t0 = int(t_sec * 1000)
    cur.execute(
        "SELECT (event_ts_ms/5000)*5 AS b, "
        "(ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]::float8 "
        "FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s "
        "AND event_ts_ms <= %s AND bid_px>0 AND ask_px>bid_px GROUP BY 1 ORDER BY 1",
        (sym + "USDT", t0 - tol_ms, t0 + tol_ms))
    g = {int(b): float(m) for b, m in cur.fetchall()}
    if not g:
        return None
    k = min(g, key=lambda x: abs(x - t_sec))
    return g[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "COALESCE(net_bp,0)::float8, COALESCE(fee_bp,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND meta_json->>'exit_path' LIKE 'stop_loss%%' "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY ts",
                (LANE, int(a.hours)))
            legs = cur.fetchall()
    print(f"近 {a.hours:.0f}h 止损出场腿 {len(legs)}")
    if not legs:
        print("无止损腿")
        return 1
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    # 各币 15s 噪声中位
    noise = {}
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for s in sorted({r[1] for r in legs}):
                cur.execute(
                    "WITH m AS (SELECT floor(event_ts_ms/15000) AS b, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 AS px "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "GROUP BY 1), d AS (SELECT px, LAG(px) OVER (ORDER BY b) AS p0 FROM m) "
                    "SELECT percentile_cont(0.5) WITHIN GROUP "
                    "(ORDER BY ABS(px-p0)/NULLIF(p0,0)*1e4)::float8 "
                    "FROM d WHERE p0 IS NOT NULL",
                    (s + "USDT", now_ms - int(a.hours * 3600_000)))
                noise[s] = float(cur.fetchone()[0] or 0.0)
            rows = []
            for ts, sym, side, net, fee in legs:
                t0 = ts.timestamp()
                base = mid_at(cur, sym, t0)
                m300 = mid_at(cur, sym, t0 + 300)
                if base is None or m300 is None or base <= 0:
                    continue
                sgn = -1.0 if str(side).lower() == "sell" else 1.0
                d_post = sgn * (m300 - base) / base * 1e4
                rows.append({"sym": sym, "net": float(net or 0.0),
                             "fee": abs(float(fee or 0.0)), "d_post": d_post,
                             "alt": float(net or 0.0) - d_post + abs(float(fee or 0.0)),
                             "noise": noise.get(sym, 0.0)})
    print(f"可算反事实 {len(rows)} 笔")
    print("=" * 96)
    print(f"{'币':6s} {'噪声bp':>7s} {'n':>5s} {'已实现bp':>9s} {'d_post300':>10s} "
          f"{'反事实bp':>9s} {'Δ(alt−now)':>11s}")
    by_sym: dict = collections.defaultdict(list)
    for r in rows:
        by_sym[r["sym"]].append(r)
    for s, v in sorted(by_sym.items(), key=lambda kv: kv[1][0]["noise"]):
        nets = [x["net"] for x in v]
        alts = [x["alt"] for x in v]
        dps = [x["d_post"] for x in v]
        d = st.mean(alts) - st.mean(nets)
        sd = st.stdev([x["alt"] - x["net"] for x in v]) if len(v) > 1 else 0.0
        t = d / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
        print(f"{s:6s} {v[0]['noise']:7.2f} {len(v):5d} {st.mean(nets):+9.2f} "
              f"{st.mean(dps):+10.2f} {st.mean(alts):+9.2f} {d:+8.2f}(t={t:+.1f})")
    # 按噪声三分
    print("=" * 96)
    allnoise = sorted({r["noise"] for r in rows})
    med = st.median([r["noise"] for r in rows]) if rows else 0.0
    low = [r for r in rows if r["noise"] <= med]
    high = [r for r in rows if r["noise"] > med]
    res = {}
    for name, grp in (("低噪声档", low), ("高噪声档", high)):
        if len(grp) < 5:
            continue
        d = st.mean(x["alt"] for x in grp) - st.mean(x["net"] for x in grp)
        sd = st.stdev([x["alt"] - x["net"] for x in grp]) if len(grp) > 1 else 0.0
        t = d / (sd / math.sqrt(len(grp))) if sd > 0 else 0.0
        print(f"{name}（噪声≤/> {med:.1f}bp）：n={len(grp)} "
              f"已实现 {st.mean(x['net'] for x in grp):+.2f}bp → 反事实 "
              f"{st.mean(x['alt'] for x in grp):+.2f}bp，Δ={d:+.2f}bp (t={t:+.2f})")
        res[name] = {"n": len(grp), "delta": round(d, 3), "t": round(t, 2)}
    print("=" * 96)
    hi = res.get("高噪声档") or {}
    lo = res.get("低噪声档") or {}
    if hi and lo:
        if hi["delta"] > 1.0 and hi.get("t", 0) > 1.0 and abs(lo["delta"]) < 1.0:
            verdict = (f"**波动自适应止损有据**：高噪声档持有更好（Δ={hi['delta']:+.2f}bp, "
                       f"t={hi['t']:+.1f}），低噪声档中性（Δ={lo['delta']:+.2f}bp）"
                       f"⇒ 可试「按 σ 缩放止损阈值」（或按币设阈值）")
        elif abs(hi["delta"]) < 1.0 and abs(lo["delta"]) < 1.0:
            verdict = ("两档都中性 ⇒ **h470 的中性结论稳健，别动止损**；"
                       "精力应全部留给入场侧（③ / 趋势闸 / 状态门控）")
        else:
            verdict = (f"高噪声档 Δ={hi['delta']:+.2f}bp、低噪声档 Δ={lo['delta']:+.2f}bp"
                       f" ⇒ 方向不一致/证据不足，暂不动止损")
    else:
        verdict = "样本不足"
    print("⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "legs": len(rows), "noise_median": round(med, 3),
         "by_coin": {s: {"n": len(v), "noise": v[0]["noise"],
                         "net": round(st.mean(x["net"] for x in v), 3),
                         "alt": round(st.mean(x["alt"] for x in v), 3)}
                     for s, v in by_sym.items()},
         "by_noise": res, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
