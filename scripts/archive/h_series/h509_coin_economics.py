"""h509：**逐币经济性画像**——为"币种重配"这个待裁项提供决策依据。

背景（两条待用户裁定的取舍之一）：h498 的分币种显著性给出
  BNB −0.70bp/腿（t=−2.57，n=678，**唯一显著为负**，且名义额最大 89k → −7.35$）、
  NEAR −3.39bp/腿（t=−1.88，且**吃掉 59% 的止损**）、ENA −3.77bp/腿、
  ARB −1.62bp/腿、**XRP −0.06bp/腿（唯一不亏）**。
但"删币/换币"会直接冲击 ≥60 腿/h 硬约束 ⇒ 需要更完整的依据才能动。

本脚本给出**可解释的机制指标**，回答"为什么某个币在亏"：
  · `vol_15s_bp`：真实逐笔的 15s 桶相邻变化（bp）——该币的噪声尺度；
  · `half_spread_bp`：盘口平均半价差（bp）——可赚的尺度；
  · **`noise_to_edge = vol_15s_bp / half_spread_bp`**：噪声/可赚比。比值越大，
    "1bp 挂宽"越是被噪声淹没（止损被打穿的概率越高）；
  · `capture_bp`：我们入场腿的实测价差捕获（是否吃满挂宽）；
  · `stop_rate`：止损往返 / 全部往返；
  · `usd_per_h`：该币每小时的美元贡献（决策量）。

判读：若 `noise_to_edge` 与 `stop_rate`/`usd_per_h` 明显相关 ⇒ 该比值可作为
"该不该在这个币上做市"的判据（与 h505 的挂宽弹性互为补充：**在噪声大的币上
不是加宽，而是减少参与**）。

用法：python scripts/h509_coin_economics.py [--hours 24]
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
OUT = ROOT / "research_l1" / "out" / "h509_coin_econ.json"


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
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
            cur.execute(
                "SELECT symbol, count(*), COALESCE(sum(net_bp*notional/1e4),0)::float8, "
                "COALESCE(avg(net_bp),0)::float8, COALESCE(stddev_samp(net_bp),0)::float8, "
                "COALESCE(sum(notional),0)::float8, "
                "count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')='') AS ents, "
                "COALESCE(avg(spread_bp) FILTER "
                "  (WHERE COALESCE(meta_json->>'exit_path','')=''),0)::float8 AS cap "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) GROUP BY 1",
                (LANE, syms, int(a.hours)))
            eng = {r[0]: r for r in cur.fetchall()}
            # 止损率：按 position 级近似（用止损腿数 / 出场腿数）
            cur.execute(
                "SELECT symbol, "
                "count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stops, "
                "count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')<>'') AS exits "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) GROUP BY 1",
                (LANE, syms, int(a.hours)))
            stops = {r[0]: (int(r[1]), int(r[2])) for r in cur.fetchall()}
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
            t0 = now_ms - int(a.hours * 3600_000)
            vol = {}
            for s in syms:
                cur.execute(
                    "WITH m AS (SELECT floor(event_ts_ms/15000) AS b, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 AS px "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "GROUP BY 1), d AS (SELECT px, LAG(px) OVER (ORDER BY b) AS p0 FROM m) "
                    "SELECT COALESCE(AVG(ABS(px-p0)/NULLIF(p0,0))*1e4,0)::float8, count(*) "
                    "FROM d WHERE p0 IS NOT NULL", (s + "USDT", t0))
                r = cur.fetchone()
                vol[s] = (float(r[0] or 0), int(r[1] or 0))
                cur.execute(
                    "SELECT COALESCE(AVG((ask_px-bid_px)/2.0/"
                    "NULLIF((ask_px+bid_px)/2.0,0))*1e4,0)::float8 "
                    "FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s "
                    "AND bid_px>0 AND ask_px>bid_px", (s + "USDT", t0))
                vol[s] = (vol[s][0], vol[s][1], float(cur.fetchone()[0] or 0))
    print(f"近 {a.hours:.0f}h 逐币经济性（在役币 {syms}）")
    print("=" * 118)
    print(f"{'币':6s} {'腿数':>6s} {'入场腿':>6s} {'净bp/腿':>8s} {'t':>6s} "
          f"{'净额$':>8s} {'$/h':>7s} {'止损率':>7s} {'噪声bp':>7s} {'半价差':>7s} "
          f"{'噪声/可赚':>9s} {'实测捕获':>8s}")
    rows = []
    for s in syms:
        e = eng.get(s)
        if not e:
            continue
        n, usd, nb, sd, noti, ents, cap = (int(e[1]), float(e[2]), float(e[3]),
                                           float(e[4] or 0), float(e[5]), int(e[6]),
                                           float(e[7]))
        se = sd / math.sqrt(n) if (sd and n > 1) else float("nan")
        t = nb / se if se and se == se and se > 0 else float("nan")
        stp, exi = stops.get(s, (0, 0))
        rate = stp / max(exi, 1)
        v, nv, hs = vol.get(s, (0.0, 0, 0.0))
        ratio = v / hs if hs else float("nan")
        print(f"{s:6s} {n:6d} {ents:6d} {nb:8.2f} {t:6.2f} {usd:8.2f} {usd/a.hours:7.3f} "
              f"{100*rate:6.1f}% {v:7.2f} {hs:7.2f} {ratio:9.2f} {cap:8.2f}")
        rows.append({"sym": s, "legs": n, "entries": ents, "net_bp": round(nb, 3),
                     "t": round(t, 2), "usd": round(usd, 3),
                     "usd_per_h": round(usd / a.hours, 4), "stop_rate": round(rate, 4),
                     "vol_15s_bp": round(v, 3), "half_spread_bp": round(hs, 3),
                     "noise_to_edge": round(ratio, 3), "capture_bp": round(cap, 3)})
    print("=" * 118)
    # 相关性（噪声/可赚 vs 止损率、vs $/h）
    def corr(xs, ys):
        if len(xs) < 3:
            return float("nan")
        mx, my = st.mean(xs), st.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        dy = math.sqrt(sum((y - my) ** 2 for y in ys))
        return num / (dx * dy) if dx and dy else float("nan")
    if len(rows) >= 3:
        c1 = corr([r["noise_to_edge"] for r in rows], [r["stop_rate"] for r in rows])
        c2 = corr([r["noise_to_edge"] for r in rows], [r["usd_per_h"] for r in rows])
        c3 = corr([r["vol_15s_bp"] for r in rows], [r["usd_per_h"] for r in rows])
        print(f"相关性：噪声/可赚 vs 止损率 r={c1:+.2f} | 噪声/可赚 vs $/h r={c2:+.2f} "
              f"| 噪声 vs $/h r={c3:+.2f}（n={len(rows)} 个币，仅供参考：样本太小）")
    worst = min(rows, key=lambda r: r["usd_per_h"]) if rows else None
    best = max(rows, key=lambda r: r["usd_per_h"]) if rows else None
    if worst and best:
        verdict = (f"最差 {worst['sym']}（{worst['usd_per_h']:+.3f}$/h，"
                   f"净{worst['net_bp']:+.2f}bp/腿 t={worst['t']:+.2f}，"
                   f"止损率 {100*worst['stop_rate']:.1f}%，噪声/可赚 {worst['noise_to_edge']:.1f}）"
                   f" vs 最好 {best['sym']}（{best['usd_per_h']:+.3f}$/h，"
                   f"净{best['net_bp']:+.2f}bp/腿，止损率 {100*best['stop_rate']:.1f}%）"
                   f"⇒ 币种间差异{'可以用』噪声/可赚』解释' if (c2 == c2 and abs(c2) > 0.5) else '**不能**只用噪声/可赚解释，需再看微观结构'}")
    else:
        verdict = "样本不足"
    print("\n⇒ 裁决:", verdict)
    print("⚠️ 决策提示：动币种/权重会直接冲击 ≥60 腿/h 硬约束 ⇒ 任何重配都应"
          "把'腾出的额度投向更好的币'作为前提，并按试跑纪律走（预注册 + 判定 + 自动回滚）。")
    OUT.write_text(json.dumps({"hours": a.hours, "rows": rows, "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
