# -*- coding: utf-8 -*-
"""[h690 2026-10-01] 方向判定深度研究:三个因子(微价/流/趋势)的真实预测力。

**方法(无选择偏差)**:不依赖我们自己的成交,而是在固定 60s 网格上取样:
  对每个网格点 t,只用"t 时刻可见的信息"预测"未来 r30/r60/r120 的中价变动",
  再按信号分桶看 E[未来收益 | 桶]。样本 = 币 × 时长 × 60 点。
  另做:①Spearman IC;②前半日拟合 OLS 权重、后半日**样本外**验证(防过拟合)。

输出:控制台表格 + research_l1/out/h690_direction_study.json
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYMBOLS = ["BNB", "UNI", "ENA", "ARB"]
HOURS = 12.0
GRID_MS = 60_000
TICK_MS = 5_000
OFI_WIN_MS = 60_000
TREND_WIN_MS = 300_000
TREND_WIN2_MS = 900_000   # [h694] 900s 趋势(对比 300s;L4 慢反转=15min 尺度)
HORIZONS = (30, 60, 120)


def _conn():
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg
    return psycopg.connect(_market_dsn(), autocommit=True)


def _mid_series(cur, sym: str, t0_ms: int, t1_ms: int) -> Dict[int, float]:
    """5s 网格中价(DB 侧下采样,取每格最后一条)。"""
    cur.execute(
        "SELECT (event_ts_ms/%s)*%s AS b,"
        " (array_agg((bid_px+ask_px)/2.0 ORDER BY event_ts_ms DESC))[1] AS mid"
        " FROM asterdex_book_ticker"
        " WHERE symbol = %s AND event_ts_ms >= %s AND event_ts_ms < %s"
        "   AND bid_px > 0 AND ask_px > bid_px"
        " GROUP BY 1 ORDER BY 1",
        (TICK_MS, TICK_MS, f"{sym}USDT", t0_ms, t1_ms))
    return {int(r[0]): float(r[1]) for r in cur.fetchall() if r[1]}


def _mp_series(cur, sym: str, t0_ms: int, t1_ms: int) -> Dict[int, float]:
    """5s 网格微价偏移(bp):mp = (bid*ask_qty + ask*bid_qty)/(bid_qty+ask_qty)。"""
    cur.execute(
        "SELECT (event_ts_ms/%s)*%s AS b,"
        " (array_agg(bid_px ORDER BY event_ts_ms DESC))[1],"
        " (array_agg(ask_px ORDER BY event_ts_ms DESC))[1],"
        " (array_agg(bid_qty ORDER BY event_ts_ms DESC))[1],"
        " (array_agg(ask_qty ORDER BY event_ts_ms DESC))[1]"
        " FROM asterdex_book_ticker"
        " WHERE symbol = %s AND event_ts_ms >= %s AND event_ts_ms < %s"
        "   AND bid_px > 0 AND ask_px > bid_px"
        " GROUP BY 1 ORDER BY 1",
        (TICK_MS, TICK_MS, f"{sym}USDT", t0_ms, t1_ms))
    out: Dict[int, float] = {}
    for b, bid, ask, bq, aq in cur.fetchall():
        if not bid or not ask or not bq or not aq:
            continue
        bid, ask, bq, aq = float(bid), float(ask), float(bq), float(aq)
        mid = (bid + ask) / 2.0
        if mid <= 0 or (bq + aq) <= 0:
            continue
        mp = (bid * aq + ask * bq) / (bq + aq)
        out[int(b)] = (mp - mid) / mid * 1e4
    return out


def _trades(cur, sym: str, t0_ms: int, t1_ms: int) -> List[Tuple[int, float, bool]]:
    cur.execute(
        "SELECT event_ts_ms, qty, is_buyer_maker FROM asterdex_trades"
        " WHERE symbol = %s AND event_ts_ms >= %s AND event_ts_ms < %s"
        " ORDER BY event_ts_ms",
        (f"{sym}USDT", t0_ms, t1_ms))
    return [(int(r[0]), float(r[1]), bool(r[2])) for r in cur.fetchall()]


def _ofi_at(trades: List[Tuple[int, float, bool]], ts_list: List[int],
            i: int, t: int) -> float:
    """(t−60s, t] 的主动买卖失衡 ∈[−1,1]（+1=全主动买）。"""
    lo = t - OFI_WIN_MS
    bv = sv = 0.0
    j = i
    while j >= 0 and ts_list[j] > lo:
        _ts, q, ibm = trades[j]
        if ibm:
            sv += q
        else:
            bv += q
        j -= 1
    return ((bv - sv) / (bv + sv)) if (bv + sv) > 0 else 0.0


def _nearest(grid: Dict[int, float], t: int, tol_ms: int,
             forward: bool = False) -> Optional[float]:
    ks = sorted(grid)
    if not ks:
        return None
    import bisect
    if forward:
        p = bisect.bisect_left(ks, t)
        if p >= len(ks) or ks[p] - t > tol_ms:
            return None
        return grid[ks[p]]
    p = bisect.bisect_right(ks, t) - 1
    if p < 0 or t - ks[p] > tol_ms:
        return None
    return grid[ks[p]]


def _spearman(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 20:
        return 0.0

    def _rank(v: List[float]) -> List[float]:
        idx = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[idx[j + 1]] == v[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r

    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return (num / (dx * dy)) if dx > 0 and dy > 0 else 0.0


def _bucket_stats(sig: List[float], ret: List[float], q: int = 5) -> List[dict]:
    if len(sig) < q * 10:
        return []
    pairs = sorted(zip(sig, ret), key=lambda p: p[0])
    n = len(pairs)
    out = []
    for k in range(q):
        seg = pairs[k * n // q:(k + 1) * n // q]
        vals = [r for _s, r in seg]
        if len(vals) < 5:
            continue
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)
        se = math.sqrt(var / len(vals)) if var > 0 else 0.0
        out.append({"q": k + 1, "n": len(vals), "mean_bp": round(m, 4),
                    "t": round(m / se, 2) if se > 0 else 0.0,
                    "lo": round(seg[0][0], 4), "hi": round(seg[-1][0], 4)})
    return out


def _ols_ic(rows: List[dict], feats: List[str], ret_key: str) -> dict:
    """前半拟合 OLS 权重 → 后半样本外 IC(标准化特征,闭式解)。"""
    n = len(rows)
    if n < 200:
        return {}
    half = n // 2
    fit, test = rows[:half], rows[half:]

    def _std(rs: List[dict]) -> Tuple[List[float], List[float]]:
        mus, sds = [], []
        for f in feats:
            v = [float(r[f]) for r in rs]
            mu = sum(v) / len(v)
            sd = math.sqrt(sum((x - mu) ** 2 for x in v) / max(1, len(v) - 1)) or 1.0
            mus.append(mu)
            sds.append(sd)
        return mus, sds

    mus, sds = _std(fit)

    def _x(r: dict) -> List[float]:
        return [(float(r[f]) - mus[i]) / sds[i] for i, f in enumerate(feats)]

    # 闭式解 (X'X)^-1 X'y,含截距
    k = len(feats) + 1
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for r in fit:
        x = [1.0] + _x(r)
        y = float(r[ret_key])
        for a in range(k):
            xty[a] += x[a] * y
            for b in range(k):
                xtx[a][b] += x[a] * x[b]
    # 高斯消元
    import copy
    A = [row[:] + [xty[i]] for i, row in enumerate(xtx)]
    for c in range(k):
        p = max(range(c, k), key=lambda r_: abs(A[r_][c]))
        A[c], A[p] = A[p], A[c]
        if abs(A[c][c]) < 1e-12:
            return {}
        pv = A[c][c]
        A[c] = [v / pv for v in A[c]]
        for r_ in range(k):
            if r_ != c and A[r_][c] != 0:
                f = A[r_][c]
                A[r_] = [a - f * b for a, b in zip(A[r_], A[c])]
    w = [A[i][k] for i in range(k)]
    preds, acts = [], []
    for r in test:
        x = [1.0] + _x(r)
        preds.append(sum(wi * xi for wi, xi in zip(w, x)))
        acts.append(float(r[ret_key]))
    return {"n_fit": len(fit), "n_test": len(test),
            "weights": {f: round(w[i + 1], 4) for i, f in enumerate(feats)},
            "intercept": round(w[0], 4),
            "oos_ic": round(_spearman(preds, acts), 4),
            "insample_ic": round(_spearman(
                [sum(wi * xi for wi, xi in zip(
                    w, [1.0] + [(float(r[f]) - mus[i]) / sds[i]
                                for i, f in enumerate(feats)]))
                 for r in fit],
                [float(r[ret_key]) for r in fit]), 4)}


def main() -> int:
    import bisect
    global GRID_MS, HORIZONS, HOURS, TICK_MS
    # [h690b] 命令行可调:--grid 15000 --horizons 5,15,30 --hours 6 --tick 5000
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--grid" and i + 1 < len(argv):
            GRID_MS = int(argv[i + 1])
        elif a == "--horizons" and i + 1 < len(argv):
            HORIZONS = tuple(int(x) for x in argv[i + 1].split(","))
        elif a == "--hours" and i + 1 < len(argv):
            HOURS = float(argv[i + 1])
        elif a == "--tick" and i + 1 < len(argv):
            TICK_MS = int(argv[i + 1])
    now_ms = int(time.time() * 1000)
    t0 = now_ms - int(HOURS * 3600 * 1000)
    rows: List[dict] = []
    per_symbol: Dict[str, int] = {}
    with _conn() as c, c.cursor() as cur:
        for sym in SYMBOLS:
            mids = _mid_series(cur, sym, t0 - TREND_WIN2_MS, now_ms)
            mps = _mp_series(cur, sym, t0 - 600_000, now_ms)
            trades = _trades(cur, sym, t0 - OFI_WIN_MS, now_ms)
            ts_list = [t for t, _q, _b in trades]
            n_before = len(rows)
            t = t0 + TREND_WIN2_MS
            while t < now_ms - max(HORIZONS) * 1000:
                i = bisect.bisect_right(ts_list, t) - 1
                ofi = _ofi_at(trades, ts_list, i, t) if i >= 0 else 0.0
                mid_now = _nearest(mids, t, 20_000)
                mid_prev = _nearest(mids, t - TREND_WIN_MS, 20_000)
                mid_prev2 = _nearest(mids, t - TREND_WIN2_MS, 20_000)
                mp = _nearest(mps, t, 20_000)
                if mid_now and mid_prev and mid_prev2 and mp is not None \
                        and mid_prev > 0 and mid_prev2 > 0:
                    rec = {
                        "symbol": sym, "ts": t,
                        "trend": (mid_now - mid_prev) / mid_prev * 1e4,
                        "trend900": (mid_now - mid_prev2) / mid_prev2 * 1e4,
                        "mp": mp,
                        "ofi": ofi,
                    }
                    ok = True
                    for h in HORIZONS:
                        mf = _nearest(mids, t + h * 1000, 20_000, forward=True)
                        if not mf:
                            ok = False
                            break
                        rec[f"r{h}"] = (mf - mid_now) / mid_now * 1e4
                    if ok:
                        rows.append(rec)
                t += GRID_MS
            per_symbol[sym] = len(rows) - n_before
            print(f"  {sym}: {per_symbol[sym]} 个样本点", flush=True)

    print(f"\n总样本 {len(rows)} 点({HOURS:.0f}h,1 分钟网格)\n")
    report: dict = {"samples": len(rows), "per_symbol": per_symbol,
                    "hours": HOURS, "signals": {}, "ols": {}}
    for sig in ("mp", "ofi", "trend", "trend900"):
        report["signals"][sig] = {}
        print(f"===== 信号 {sig} =====")
        for h in HORIZONS:
            xs = [float(r[sig]) for r in rows]
            ys = [float(r[f"r{h}"]) for r in rows]
            ic = _spearman(xs, ys)
            buckets = _bucket_stats(xs, ys)
            report["signals"][sig][f"r{h}"] = {"ic": round(ic, 4),
                                               "buckets": buckets}
            print(f"  r{h:<4} IC={ic:+.4f}   分桶均值(bp): "
                  + "  ".join(f"Q{b['q']}={b['mean_bp']:+.3f}(t={b['t']:+.1f})"
                              for b in buckets))
        print()
    # [h692] **VPIN(毒性)秒级标定**:VPIN = 近 20 桶 |OFI| 均值。
    # 背景:h689 修好流因子后 VPIN 闸**首次真正工作**(此前 OFI≡0 ⇒ VPIN 恒 0),
    # 阈值 0.6 从未被真实行情验证,现在拦掉 83% 的 tick。用数据定阈值。
    print("\n===== VPIN(20桶|OFI|均值)标定 =====")
    n20 = 20
    for i, r in enumerate(rows):
        seg = rows[max(0, i - n20 + 1): i + 1]
        r["vpin"] = sum(abs(float(x["ofi"])) for x in seg) / len(seg)
    for h in (15, 30):
        xs = [float(r["vpin"]) for r in rows]
        ys = [float(r[f"r{h}"]) for r in rows]
        ic = _spearman(xs, ys)
        buckets = _bucket_stats(xs, ys)
        print(f"  r{h:<4} IC={ic:+.4f}   VPIN 分桶均值(bp): "
              + "  ".join(f"Q{b['q']}={b['mean_bp']:+.3f}(t={b['t']:+.1f})"
                          for b in buckets))
    print("\n  VPIN 阈值扫描(VPIN ≥ θ 时封加仓,看被封段的收益):")
    for th in (0.4, 0.5, 0.6, 0.7, 0.8):
        keep = [r for r in rows if float(r["vpin"]) < th]
        if len(keep) < 100:
            continue
        m = sum(float(r["r15"]) for r in keep) / len(keep)
        print(f"    θ={th:.1f}  放行 {100.0*len(keep)/len(rows):5.1f}%  "
              f"放行段 r15 均值 {m:+.4f}bp")
    print("  (放行段均值越高 = 阈值越合理;放行比例太低 = 阈值过严把正常时段也封了)")

    print("\n===== 多元 OLS(前半拟合 → 后半样本外) =====")
    for h in HORIZONS:
        res = _ols_ic(rows, ["mp", "ofi", "trend", "trend900"], f"r{h}")
        if res:
            report["ols"][f"r{h}"] = res
            print(f"  r{h:<4} 权重={res['weights']} 样本内IC={res['insample_ic']:+.4f}"
                  f"  **样本外IC={res['oos_ic']:+.4f}** (n={res['n_test']})")

    # [h690b] **否决阈值标定**:用当前线上权重构造 D,按 |D| 分档看未来收益,
    # 并扫阈值给出"保留集合的平均收益 vs 保留比例" ⇒ 数据定 dir_min_abs。
    print("\n===== 否决阈值标定(当前线上权重 0.30/0.20/0.50) =====")
    MP_SAT, TR_SAT = 2.0, 20.0

    def _d_cur(r: dict) -> float:
        mp = max(-1.0, min(1.0, float(r["mp"]) / MP_SAT))
        of = max(-1.0, min(1.0, float(r["ofi"])))
        tr = max(-1.0, min(1.0, float(r["trend"]) / TR_SAT))
        return 0.30 * mp + 0.20 * of + 0.50 * tr

    for r in rows:
        r["d_cur"] = _d_cur(r)
    h_main = HORIZONS[-1]
    dec = _bucket_stats([abs(r["d_cur"]) for r in rows],
                        [float(r[f"r{h_main}"]) for r in rows], q=10)
    report["threshold_calib"] = {"horizon": h_main, "deciles": dec}
    print(f"  |D| 十分位 → r{h_main} 均值(bp):")
    for b in dec:
        print(f"    D{b['q']:>2}: |D|∈[{b['lo']:.3f},{b['hi']:.3f}] n={b['n']:>5} "
              f"r={b['mean_bp']:+.3f}(t={b['t']:+.1f})")
    print(f"\n  阈值扫描(保留 |D| ≥ θ 的样本):")
    scan = []
    for th in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60):
        keep = [r for r in rows if abs(r["d_cur"]) >= th]
        if len(keep) < 100:
            continue
        vals = [float(r[f"r{h_main}"]) for r in keep]
        m = sum(vals) / len(vals)
        scan.append({"theta": th, "n": len(keep),
                     "keep_pct": round(100.0 * len(keep) / len(rows), 1),
                     "mean_bp": round(m, 4)})
        print(f"    θ={th:.2f}  保留 {100.0*len(keep)/len(rows):5.1f}%  "
              f"r{h_main} 均值 {m:+.4f}bp  (n={len(keep)})")
    report["threshold_calib"]["scan"] = scan
    out = ROOT / "research_l1" / "out" / "h690_direction_study.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写 {out}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
