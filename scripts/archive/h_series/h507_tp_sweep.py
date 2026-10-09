"""h507：**止盈距离**该多远？——MFE/MAE 分析 + 反事实（从未测过的杠杆）。

动机：h506 显示强趋势里被动回摆收口从 86% 崩到 30%、止损升到 16%，
而 `take_profit_taker` 是**最赚的一条出口**（+49.92bp/腿）。
现行 `take_profit_bp=30` 极少触发（12h 仅 5 腿）⇒ 大量往返"曾经浮盈过又回吐"。
这与早前结论（"出场在均值回归上等"）一致：等到 +30bp 才走，往往等不到。

本脚本先给**数据**、再给**有边界的反事实**：

  ① 数据：对每趟往返（h494 累计归零口径）用真实中价路径算
     MFE（最大有利偏移 bp）与 MAE（最大不利偏移 bp），并与实际实现值对照；
  ② 反事实：对止盈档 X ∈ {5,8,12,20,30,50}，若"路径先触及 +X bp 就按被动价出场"
     （maker 费 0，不加滑点），该往返的 P&L 变成多少；
     未触及的沿用实际实现值。给出总盈亏变化与触发比例。

⚠️ 反事实的**已知乐观项**（必须与结论一起看）：
  · 假设被动挂单必然成交（真实里可能等不到 ⇒ 实际收益低于估计）；
  · 未建模"提前平仓腾出额度 ⇒ 腿速上升"的正效应（也没建模其风险）；
  · 中价路径取自 5s 桶，触及判定比真实挂单粗糙。
  因此只把它当作**方向性证据**：若某档显著更优，才值得开试跑。

用法：python scripts/h507_tp_sweep.py [--hours 48]
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
OUT = ROOT / "research_l1" / "out" / "h507_tp_sweep.json"
GRID = (5, 8, 12, 20, 30, 50)


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
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), COALESCE(net_bp,0)::float8, "
                "COALESCE(notional,0)::float8, "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8 "
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
        for ts, _s, side, qty, ep, net, noti, px in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            cum += signed
            peak = max(peak, abs(cum))
            if t is None:
                t = {"sym": sym, "open_ts": ts, "usd": 0.0, "noti": 0.0,
                     "entry_notional": 0.0, "entry_cost": 0.0, "dir": 0.0}
            t["usd"] += float(net or 0.0) * float(noti or 0.0) / 1e4
            t["noti"] += float(noti or 0.0)
            # 入场腿（非 flatten 且不减少持仓）用于算均价与方向
            if ep == "" and (abs(cum - signed) < 1e-9 or (cum - signed > 0) == (signed > 0)):
                t["entry_notional"] += abs(float(qty)) * float(px)
                t["entry_cost"] += abs(float(qty))
                t["dir"] += signed
            if abs(cum) <= max(1e-6, 1e-4 * max(peak, 1e-9)):
                t["exit_ts"] = ts
                t["avg_entry"] = (t["entry_notional"] / t["entry_cost"]
                                  if t["entry_cost"] else 0.0)
                t["sign"] = 1.0 if t["dir"] > 0 else (-1.0 if t["dir"] < 0 else 0.0)
                t["bp"] = (t["usd"] / t["noti"] * 1e4) if t["noti"] else 0.0
                t["hold"] = (ts - t["open_ts"]).total_seconds()
                trips.append(t)
                t = None
                peak = 0.0
    trips = [t for t in trips if t.get("avg_entry") and t.get("sign")]
    # ⚠️ 过滤**分段粘连**（h494 已知：残量未归零会把多笔往返粘成一段）：
    # 引擎单币敞口上限 = max_net_directional_ratio(2.0) × 权益 ≈ 566$，
    # 任何"入场名义额"远超该上限的段一定是多笔往返的拼接 ⇒ 剔除，
    # 否则反事实会把 X bp 算到数千美元的"持仓"上（第一版就是这样算出 +1857$ 的）。
    SIZE_CAP = 900.0
    _before = len(trips)
    trips = [t for t in trips if 0 < t["entry_notional"] <= SIZE_CAP]
    print(f"剔除粘连段（入场名义 > {SIZE_CAP:.0f}$）: {_before - len(trips)} 段，"
          f"保留 {len(trips)}")
    print(f"近 {a.hours:.0f}h：可用往返 {len(trips)}")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    mfe_list, mae_list = [], []
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for t in trips:
                t0 = int(t["open_ts"].timestamp() * 1000)
                t1 = int(t["exit_ts"].timestamp() * 1000)
                cur.execute(
                    "SELECT (event_ts_ms/5000)*5 AS b, "
                    "(ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]::float8 "
                    "FROM asterdex_book_ticker WHERE symbol=%s "
                    "AND event_ts_ms >= %s AND event_ts_ms <= %s "
                    "AND bid_px>0 AND ask_px>bid_px GROUP BY 1 ORDER BY 1",
                    (t["sym"] + "USDT", t0, t1))
                g = [float(m) for _b, m in cur.fetchall() if m]
                if not g:
                    t["mfe"] = t["mae"] = None
                    continue
                e = t["avg_entry"]
                s = t["sign"]
                mfe = max((m - e) / e * 1e4 * s for m in g)
                mae = min((m - e) / e * 1e4 * s for m in g)
                t["mfe"], t["mae"] = mfe, mae
                mfe_list.append(mfe)
                mae_list.append(mae)
    ok = [t for t in trips if t.get("mfe") is not None]
    print(f"算出路径的往返 {len(ok)}")
    if not ok:
        print("无路径数据")
        return 1
    print("=" * 92)
    mf = sorted(mfe_list)
    ma = sorted(mae_list)
    def q(v, p):
        return v[min(len(v) - 1, int(p * (len(v) - 1)))]
    print(f"MFE（最大有利偏移）p50={q(mf,.5):+.1f} p75={q(mf,.75):+.1f} "
          f"p90={q(mf,.9):+.1f} max={mf[-1]:+.1f} bp")
    print(f"MAE（最大不利偏移）p50={q(ma,.5):+.1f} p25={q(ma,.25):+.1f} "
          f"p10={q(ma,.10):+.1f} min={ma[0]:+.1f} bp")
    realized = [t["bp"] for t in ok]
    print(f"实际实现 往返加权bp 均值={st.mean(realized):+.2f} 中位={st.median(realized):+.2f}")
    # 关键切片：MFE 够大但最终没赚到的比例
    print("\n「曾经浮盈过，但最终没赚到」的比例：")
    for X in (5, 8, 12, 20, 30):
        hit = [t for t in ok if t["mfe"] >= X]
        if not hit:
            continue
        bad = [t for t in hit if t["bp"] <= 0]
        print(f"  MFE ≥ {X:2d}bp 的往返 {len(hit):4d} 趟（占 {100*len(hit)/len(ok):4.1f}%），"
              f"其中最终 ≤0bp 的 {len(bad):4d} 趟（{100*len(bad)/max(len(hit),1):4.1f}%）"
              f" ⇒ 合计回吐 {sum(t['usd'] for t in bad):+.2f}$")
    # 反事实：触及 +X 即以被动价出场
    print("\n" + "=" * 92)
    print("反事实：路径先触及 +X bp ⇒ 该往返按 +X bp 结算（被动费 0，不含滑点）")
    print(f"{'X(bp)':>6s} {'触发往返':>8s} {'触发占比':>8s} {'总盈亏$':>10s} "
          f"{'相对现状Δ$':>10s} {'每往返Δ$':>9s}")
    base = sum(t["usd"] for t in ok)
    print(f"{'现状':>6s} {'':>8s} {'':>8s} {base:10.2f} {'—':>10s} {'—':>9s}")
    results = {}
    for X in GRID:
        tot = 0.0
        trig = 0
        for t in ok:
            if t["mfe"] >= X:
                # ⚠️ 口径修正（第一版错得很离谱：1587 趟算出 +2856$）：
                # 结算基准必须是**持仓规模**（= 开仓腿名义额之和），
                # 不能用 `t["noti"]`（那是所有腿名义额之和，含出场腿，重复计数 2–4 倍）。
                size = t["entry_notional"] or t["noti"]
                tot += X * size / 1e4
                trig += 1
            else:
                tot += t["usd"]
        results[X] = {"triggered": trig, "usd": round(tot, 3),
                      "delta": round(tot - base, 3)}
        print(f"{X:6d} {trig:8d} {100.0*trig/len(ok):7.1f}% {tot:10.2f} "
              f"{tot-base:+10.2f} {(tot-base)/len(ok):+9.4f}")
    best = max(results.items(), key=lambda kv: kv[1]["usd"])
    cur = results.get(30) or {}
    print("\n⇒ 最优档:", best[0], "bp（Δ=", f"{best[1]['delta']:+.2f}$", "）")
    hrs = a.hours
    per_h = best[1]["delta"] / max(hrs, 1e-9)
    # 判读阈值要与证据量级匹配（第一版写 Δ>3$ 就"值得试跑"，过于宽松）：
    # 参考量级：现行亏损 ≈ −1.2$/h，所以 <0.3$/h 的收益属于**次要杠杆**。
    if best[1]["delta"] > 20.0 and per_h > 0.4 and best[0] > 30:
        verdict = (f"止盈 {best[0]}bp 反事实 {best[1]['delta']:+.2f}$"
                   f"（{per_h:+.2f}$/h，触发 {best[1]['triggered']} 趟）⇒ 值得开试跑")
    elif best[1]["delta"] > 0.5 and best[0] >= 30:
        verdict = (f"**次要杠杆**：最优档 {best[0]}bp 仅 {best[1]['delta']:+.2f}$"
                   f"（{per_h:+.2f}$/h）；且**收紧止盈明显有害**"
                   f"（5bp 档 {results.get(5, {}).get('delta', 0):+.2f}$）"
                   f"⇒ 不做单独试跑，排在入场侧（③/趋势闸）之后")
    else:
        verdict = ("反事实无改善甚至更差 ⇒ **不动止盈**；"
                   "回吐是入场质量问题的表象，不是止盈设得太远")
    print("⇒ 裁决:", verdict)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "trips": len(ok),
         "mfe": {"p50": q(mf, .5), "p90": q(mf, .9), "max": mf[-1]},
         "mae": {"p50": q(ma, .5), "p10": q(ma, .1), "min": ma[0]},
         "realized_mean_bp": round(st.mean(realized), 3),
         "base_usd": round(base, 3),
         "counterfactual": {str(k): v for k, v in results.items()},
         "best": {"x": best[0], **best[1]}, "verdict": verdict},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
