"""h519：**止损队列的入场市况画像**——能不能在入场侧提前识别？

为什么这是最后一根有决策价值的线：
  · 现行配置下亏损 100% 来自止损（h497/h496），而止损与"不止损"EV 相当（h470）
    ⇒ 亏损由**入场**决定；
  · 波动闸（`vol_pause`/`vol_regime`）只对 2/5 的币生效（h501：BNB/NEAR/ENA
    没有 `vol_baseline_bp` ⇒ `sigma_norm` 恒为 0 ⇒ **从未被波动闸管过**）；
  · 若"会被止损"的往返在**入场时刻**就有可辨识的市况特征（近期噪声高、刚发生跳变、
    点差突然张开…），就可以在入场侧加闸——不必动止损、也不必删币。

口径：用 h494 累计归零重建往返；每趟往返取**首腿时刻 −45s**（h488 延迟校正），
用真实逐笔算：
  · `noise_1m`：过去 60s 内 15s 桶相邻变化的绝对值均值（近期噪声）；
  · `noise_5m`：过去 300s 同口径；
  · `r60` / `r300`：过去 60s / 300s 的价格变化绝对值；
  · `burst`：过去 60s 内是否出现 ≥3× 该币噪声中位的单步跳变；
比较**止损收口**与**其余收口**的分布（中位 + Mann-Whitney 近似 t）。

用法：python scripts/h519_stop_entry_state.py [--hours 72]
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
OUT = ROOT / "research_l1" / "out" / "h519_stop_entry_state.json"
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


def welch(a, b):
    if len(a) < 5 or len(b) < 5:
        return None
    m1, m2 = st.mean(a), st.mean(b)
    v1 = st.variance(a) if len(a) > 1 else 0.0
    v2 = st.variance(b) if len(b) > 1 else 0.0
    se = math.sqrt(v1 / len(a) + v2 / len(b))
    return (m1 - m2, (m1 - m2) / se if se > 0 else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path','') "
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
        for ts, _s, side, qty, ep in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            cum += signed
            peak = max(peak, abs(cum))
            if t is None:
                t = {"sym": sym, "open_ts": ts, "paths": collections.Counter()}
            if ep:
                t["paths"][ep] += 1
            if abs(cum) <= max(1e-6, 1e-4 * max(peak, 1e-9)):
                t["closed_by"] = t["paths"].most_common(1)[0][0] if t["paths"] else "REDUCE"
                trips.append(t)
                t = None
                peak = 0.0
    print(f"近 {a.hours:.0f}h 往返 {len(trips)}")
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    stops, others = [], []
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for t in trips:
                t_ms = int((t["open_ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                cur.execute(
                    "WITH m AS (SELECT floor(event_ts_ms/15000) AS b, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 AS px "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "AND event_ts_ms <= %s GROUP BY 1) "
                    "SELECT b, px FROM m ORDER BY b",
                    (t["sym"] + "USDT", t_ms - 360000, t_ms))
                b = [(int(x[0]), float(x[1])) for x in cur.fetchall()]
                if len(b) < 8:
                    continue
                steps = [abs(b[i][1] - b[i - 1][1]) / b[i - 1][1] * 1e4
                         for i in range(1, len(b)) if b[i - 1][1] > 0]
                last4 = steps[-4:]
                last20 = steps[-20:]
                t["noise_1m"] = st.mean(last4) if last4 else 0.0
                t["noise_5m"] = st.mean(last20) if last20 else 0.0
                t["r60"] = abs(b[-1][1] - b[-5][1]) / b[-5][1] * 1e4 if len(b) >= 5 else 0.0
                t["r300"] = abs(b[-1][1] - b[0][1]) / b[0][1] * 1e4
                med = st.median(steps) if steps else 0.0
                t["burst"] = 1.0 if (med > 0 and max(last4 or [0]) >= 3 * med) else 0.0
                (stops if t["closed_by"].startswith("stop_loss") else others).append(t)
    print(f"止损收口 {len(stops)} / 其余 {len(others)}")
    if len(stops) < 5:
        print("止损样本不足")
        return 1
    print("=" * 100)
    print(f"{'特征':>10s} {'止损中位':>10s} {'其余中位':>10s} {'Δ(均值)':>10s} {'t':>7s} "
          f"{'止损均值':>10s} {'其余均值':>10s}")
    res = {}
    for k in ("noise_1m", "noise_5m", "r60", "r300", "burst"):
        s1 = [x[k] for x in stops if k in x]
        s2 = [x[k] for x in others if k in x]
        if not s1 or not s2:
            continue
        w = welch(s1, s2)
        print(f"{k:>10s} {st.median(s1):10.2f} {st.median(s2):10.2f} "
              f"{w[0]:+10.2f} {w[1]:+7.2f} {st.mean(s1):10.2f} {st.mean(s2):10.2f}")
        res[k] = {"stop_median": round(st.median(s1), 3),
                  "other_median": round(st.median(s2), 3),
                  "delta_mean": round(w[0], 3), "t": round(w[1], 2)}
    print("=" * 100)
    sig = {k: v for k, v in res.items() if abs(v["t"]) >= 1.5}
    print(f"显著特征（|t|≥1.5）：{sig if sig else '无'}")
    # 按噪声分档看止损率
    all_noise = sorted(x["noise_1m"] for x in stops + others if "noise_1m" in x)
    if all_noise:
        q = all_noise[len(all_noise) // 2]
        lo = [x for x in stops + others if x.get("noise_1m", 0) <= q]
        hi = [x for x in stops + others if x.get("noise_1m", 0) > q]
        for nm, grp in (("低噪声半", lo), ("高噪声半", hi)):
            n = len(grp)
            s = sum(1 for x in grp if x["closed_by"].startswith("stop_loss"))
            print(f"  {nm}（noise_1m ≤/> {q:.2f}bp）：n={n} 止损 {s} "
                  f"⇒ 止损率 {100.0*s/max(n,1):.1f}%")
    if sig:
        verdict = (f"**入场市况可辨识**：{list(sig)} 在止损队列与其余之间存在显著差异 "
                   f"⇒ 可在**入场侧**加闸（不必动止损/删币）；应预注册试跑验证")
    else:
        verdict = ("入场市况**不可辨识**（无 |t|≥1.5 的特征）⇒ 止损不是「入场时点选错」"
                   "能解释的，方向应转向**逐币几何/状态门控**或接受现状")
    # ── 取舍曲线：按 noise_1m 分位设闸，看"砍掉多少往返 × 去掉多少止损" ──
    pooled = [x for x in (stops + others) if "noise_1m" in x]
    if pooled:
        vals = sorted(x["noise_1m"] for x in pooled)
        print("\n取舍曲线（按「近 1min 噪声」设闸：只做 noise ≤ 阈值 的时段）")
        print("=" * 100)
        print(f"{'阈值(bp)':>9s} {'保留往返':>8s} {'占比':>7s} {'保留止损':>8s} "
              f"{'被砍止损':>8s} {'保留档止损率':>12s} {'被砍档止损率':>12s}")
        tradeoff = []
        for pct in (0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95):
            thr = vals[min(len(vals) - 1, int(len(vals) * pct))]
            keep = [x for x in pooled if x["noise_1m"] <= thr]
            drop = [x for x in pooled if x["noise_1m"] > thr]
            ks = sum(1 for x in keep if x["closed_by"].startswith("stop_loss"))
            ds = sum(1 for x in drop if x["closed_by"].startswith("stop_loss"))
            print(f"{thr:9.2f} {len(keep):8d} {100.0*len(keep)/len(pooled):6.1f}% "
                  f"{ks:8d} {ds:8d} {100.0*ks/max(len(keep),1):11.2f}% "
                  f"{100.0*ds/max(len(drop),1):11.2f}%")
            tradeoff.append({"threshold_bp": round(thr, 3), "keep": len(keep),
                             "keep_stops": ks, "dropped_stops": ds,
                             "keep_stop_rate": round(ks / max(len(keep), 1), 4)})
        OUT_T = tradeoff
    else:
        OUT_T = []
    # ── 关键：**相对口径**是否同样有效？（决定"修波动基准"能否替代新增绝对闸）──
    # 引擎的 `vol_pause` 用的是 `sigma_norm = vol_cur / vol_baseline − 1`（相对该币常态）。
    # 若"噪声 / 该币噪声中位"与"绝对噪声"有同样的分辨力 ⇒ 修基准即可，不必加新参数。
    coin_med = {}
    for s in {x["sym"] for x in stops + others}:
        v = [x["noise_1m"] for x in (stops + others) if x["sym"] == s and "noise_1m" in x]
        if len(v) >= 20:
            coin_med[s] = st.median(v)
    for x in stops + others:
        if "noise_1m" in x and x["sym"] in coin_med and coin_med[x["sym"]] > 0:
            x["noise_rel"] = x["noise_1m"] / coin_med[x["sym"]]
    pooled_r = [x for x in (stops + others) if x.get("noise_rel")]
    if pooled_r:
        print("\n**相对口径**（噪声 / 该币噪声中位）的取舍曲线")
        print("=" * 100)
        print(f"{'倍数':>7s} {'保留往返':>8s} {'占比':>7s} {'保留止损':>8s} "
              f"{'被砍止损':>8s} {'保留档止损率':>12s}")
        vals_r = sorted(x["noise_rel"] for x in pooled_r)
        rel_tradeoff = []
        for pct in (0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95):
            thr = vals_r[min(len(vals_r) - 1, int(len(vals_r) * pct))]
            keep = [x for x in pooled_r if x["noise_rel"] <= thr]
            drop = [x for x in pooled_r if x["noise_rel"] > thr]
            ks = sum(1 for x in keep if x["closed_by"].startswith("stop_loss"))
            ds = sum(1 for x in drop if x["closed_by"].startswith("stop_loss"))
            print(f"{thr:7.2f} {len(keep):8d} {100.0*len(keep)/len(pooled_r):6.1f}% "
                  f"{ks:8d} {ds:8d} {100.0*ks/max(len(keep),1):11.2f}%")
            rel_tradeoff.append({"multiple": round(thr, 3), "keep": len(keep),
                                 "keep_stops": ks, "dropped_stops": ds,
                                 "keep_stop_rate": round(ks / max(len(keep), 1), 4)})
        print(f"（各币噪声中位："
              + "、".join(f"{k}={v:.2f}bp" for k, v in sorted(coin_med.items())) + "）")
        OUT_R = rel_tradeoff
    else:
        OUT_R = []
    # ── 辛普森检验：绝对口径的分辨力是「币内择时」还是「跨币构成」？ ──
    # 若币内高低两半止损率无差异 ⇒ 绝对噪声只是 NEAR/ENA 的代理变量 ⇒
    # 正确杠杆是逐币几何/剔币，而不是时段闸。
    print("\n**币内分层**（每币按自身噪声中位切两半）——区分「择时」与「构成」")
    print("=" * 100)
    print(f"{'币':>6s} {'往返':>6s} {'止损':>5s} {'止损率':>7s} | "
          f"{'低半n':>6s} {'低半止损':>8s} | {'高半n':>6s} {'高半止损':>8s} | "
          f"{'币内提升':>9s}")
    within = []
    for s in sorted({x["sym"] for x in stops + others}):
        g = [x for x in (stops + others) if x["sym"] == s and "noise_1m" in x]
        if len(g) < 20:
            continue
        m = st.median([x["noise_1m"] for x in g])
        lo = [x for x in g if x["noise_1m"] <= m]
        hi = [x for x in g if x["noise_1m"] > m]
        sl = sum(1 for x in lo if x["closed_by"].startswith("stop_loss"))
        sh = sum(1 for x in hi if x["closed_by"].startswith("stop_loss"))
        ns = sum(1 for x in g if x["closed_by"].startswith("stop_loss"))
        rl, rh = sl / max(len(lo), 1), sh / max(len(hi), 1)
        lift = (rh / rl) if rl > 0 else float("inf") if rh > 0 else 1.0
        print(f"{s:>6s} {len(g):6d} {ns:5d} {100.0*ns/len(g):6.2f}% | "
              f"{len(lo):6d} {sl:8d} | {len(hi):6d} {sh:8d} | {lift:9.2f}")
        within.append({"sym": s, "n": len(g), "stops": ns, "median_noise": round(m, 3),
                       "lo_n": len(lo), "lo_stops": sl, "hi_n": len(hi), "hi_stops": sh,
                       "lift": (round(lift, 3) if lift != float("inf") else None)})
    tlo_n = sum(w["lo_n"] for w in within)
    tlo_s = sum(w["lo_stops"] for w in within)
    thi_n = sum(w["hi_n"] for w in within)
    thi_s = sum(w["hi_stops"] for w in within)
    print(f"\n  合并**币内**：低半 {tlo_s}/{tlo_n} = {100.0*tlo_s/max(tlo_n,1):.2f}% "
          f"| 高半 {thi_s}/{thi_n} = {100.0*thi_s/max(thi_n,1):.2f}%")
    # 跨币对照：各币止损率 vs 各币噪声中位 的相关（手算 Pearson，避免依赖 scipy）
    if len(within) >= 3:
        _xs = [w["median_noise"] for w in within]
        _ys = [w["stops"] / max(w["n"], 1) for w in within]
        _mx, _my = st.mean(_xs), st.mean(_ys)
        _sxy = sum((a - _mx) * (b - _my) for a, b in zip(_xs, _ys))
        _sxx = sum((a - _mx) ** 2 for a in _xs)
        _syy = sum((b - _my) ** 2 for b in _ys)
        _r = _sxy / ((_sxx * _syy) ** 0.5) if _sxx > 0 and _syy > 0 else 0.0
        print(f"  跨币对照：币噪声中位 ↔ 币止损率 相关 r={_r:+.3f}（n={len(within)}，"
              f"仅参考，样本小）")
        OUT_C = {"within": within, "pooled_lo": [tlo_s, tlo_n],
                 "pooled_hi": [thi_s, thi_n], "coin_corr": round(_r, 3)}
    else:
        OUT_C = {"within": within}
    pooled_all = tlo_n + thi_n
    if pooled_all > 0:
        lo_rate = tlo_s / max(tlo_n, 1)
        hi_rate = thi_s / max(thi_n, 1)
        if hi_rate >= 2.0 * max(lo_rate, 1e-9) and thi_s >= 10:
            print("  ⇒ 币内分层**仍成立** ⇒ 存在真实的择时效应，绝对噪声闸有独立价值")
        else:
            print("  ⇒ 币内分层**基本消失** ⇒ 绝对噪声主要是「币种构成」的代理 ⇒ "
                  "**不新增时段闸**；杠杆应放在逐币几何/剔币（与 h512 的 f* 排序一致）")
    # ── 遗漏检查：币内表覆盖了多少趟？缺的币去哪了？ ──
    _cov = {}
    for x in stops + others:
        _cov.setdefault(x["sym"], [0, 0])
        _cov[x["sym"]][0] += 1
        if "noise_1m" in x:
            _cov[x["sym"]][1] += 1
    print("\n覆盖检查（往返数 / 其中有噪声特征）")
    for s, (n, k) in sorted(_cov.items(), key=lambda kv: -kv[1][0]):
        flag = "" if k >= 20 else "  ← 未进币内表（样本<20 或缺特征）"
        print(f"  {s:>6s} {n:5d} / {k:5d}{flag}")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps({"hours": a.hours, "trips": len(trips),
                               "stops": len(stops), "features": res,
                               "tradeoff_absolute": OUT_T,
                               "tradeoff_relative": OUT_R,
                               "simpson": OUT_C,
                               "verdict": verdict}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
