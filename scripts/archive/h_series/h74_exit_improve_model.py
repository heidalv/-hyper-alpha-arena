"""H74：出库腿的成交概率 vs 改进最优价的代价 —— 修 13% 强平率的直接依据。

# 问题（H72 实测）

    强平 11.5% 的笔数 → **88.0%** 的亏损，每笔 **−12.73bp**
    成本构成：穿越半价差 ~1.8bp + **taker 4bp** + 持仓不利移动 ~6.9bp

若强平率→0，边际就是 **+1.3bp/笔**（正）。**不需要新 alpha，只需别被迫市价平仓。**

# 现状与机会

当前出库腿挂在 mid 附近，实测被动成交率仅 **37.5%**（H67）
⇒ 62.5% 打不出去 ⇒ 撞 60s 上限 ⇒ 强平。

Arroyo et al. QF 24(1):35–57 (2024) Table 3：**改善最优价使成交概率 8.2 倍**。
⇒ 出库腿应该**改进最优价**（挂在 best_ask 内侧），而不是挂在 mid。
代价是成交时少赚的价差。本脚本测这条权衡曲线。

# 成交判据（保守，避免自欺）

出库是"卖"。我们的卖价 `Q = ask − f×(ask−bid)`（f=0 挂 best_ask，f=1 挂 mid）。
**保守判据**：只有当**真实 ask 已经走到 ≤ Q**（即市场价格下行到我们的报价）
**且该秒存在主动买**时，才算成交。
—— 这样不会假设"我们挂进去就必然被优先成交"（那需要队列位置假设，H43 的错误）。

# 统计口径

分母 = **全部入场事件**（含最终强平），**不做任何样本排除**（第 16/19 条教训）。
强平按 taker 出场：卖在 `mid − 半价差`，扣 taker 费。

用法：
    .venv\\Scripts\\python.exe scripts\\h74_exit_improve_model.py --hours 48
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h74_exit_improve.json"
STEP_MS = 15_000
MAX_H = 180
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def collect(sym, hours, entry_frac):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = sym if sym.endswith("USDT") else f"{sym}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    ob = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(ob) < 5000 or len(tr) < 500:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob])
    oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]
    mid = 0.5 * (ob_ + oa)
    half = 0.5 * (oa - ob_)

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]

    # 主动买成交（is_buyer_maker=False）的价格序列 —— 用于判"能否成交我们的卖单"
    buy_mask = ~tbm
    tb_t = tt[buy_mask]
    tb_p = tp[buy_mask]
    # 每个时刻"最近 1 秒内主动买的最高价"：用排序 + 滑动
    # 简化且保守：对查询时刻 x，取 (x-1000, x] 内主动买的最高价
    if len(tb_t) < 10:
        return None

    t0, t1 = int(ot[0]), int(ot[-1])
    grids = np.arange(t0 + 600_000, t1 - (MAX_H + 5) * 1000, STEP_MS)
    gi = np.searchsorted(ot, grids, side="right") - 1
    g = gi >= 0
    grids, gi = grids[g], gi[g]
    if len(grids) < 100:
        return None

    mb, ma = ob_[gi], oa[gi]
    mid_g = 0.5 * (mb + ma)
    half_g = 0.5 * (ma - mb)
    px_bid = np.minimum(mid_g - float(entry_frac) * half_g, mid_g)

    # 入场事件：主动卖打到我们的买价
    ev = []
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + STEP_MS
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg = slice(i0, i1)
        if not (tbm[seg] & (tp[seg] <= px_bid[k])).any():
            continue
        ev.append(k)
    if len(ev) < 50:
        return None
    ev = np.array(ev)
    ev_ts = grids[ev]
    ev_bid = px_bid[ev]
    ev_mid = mid_g[ev]

    # 前向路径（1s 分辨率）
    offs = np.arange(1, MAX_H + 1) * 1000
    path_ms = ev_ts[:, None] + offs[None, :]
    pj = np.searchsorted(ot, path_ms.ravel(), side="right") - 1
    pj = np.clip(pj, 0, len(ot) - 1)
    p_ask = oa[pj].reshape(path_ms.shape)
    p_bid = ob_[pj].reshape(path_ms.shape)
    p_mid = mid[pj].reshape(path_ms.shape)

    # 每个路径点的"过去 1 秒主动买最高价"。
    # ⚠️ 不要逐点循环（numpy 标量索引约 1.5µs/次 × 800 万点 = 十几秒），
    # 用 pandas 的 rolling（C 实现）+ reindex(method='pad') 一次算完。
    try:
        import pandas as pd
        ts_series = pd.Series(tb_p, index=pd.to_datetime(tb_t, unit="ms"))
        rmax = ts_series.rolling("1000ms").max()          # 右闭 1 秒窗口最高主动买价
        qidx = pd.to_datetime(path_ms.ravel(), unit="ms")
        aligned = rmax.reindex(qidx, method="pad")        # 取"≤ 查询时刻"的窗口值
        maxbuy = aligned.to_numpy(dtype=float).reshape(path_ms.shape)
        # 注意：pad 只会取到"已存在的 rolling 结果"，若两笔成交间隔 >1s，
        # rolling 在该点为空 ⇒ pad 会沿用更早的值。为安全，把"窗口内确实有成交"
        # 的条件也带上：用 searchsorted 判定 (q−1000, q] 内是否有主动买。
        _hi = np.searchsorted(tb_t, path_ms.ravel(), side="right")
        _lo = np.searchsorted(tb_t, path_ms.ravel() - 1000, side="right")
        _none = (_hi <= _lo).reshape(path_ms.shape)
        maxbuy[_none] = np.nan
    except Exception as e:
        print(f"  [warn] pandas rolling 不可用（{e}），退化为逐点（会慢）")
        hi_idx = np.searchsorted(tb_t, path_ms.ravel(), side="right")
        lo_idx = np.searchsorted(tb_t, (path_ms.ravel() - 1000), side="right")
        maxbuy = np.full(path_ms.size, np.nan)
        for i in range(path_ms.size):
            if hi_idx[i] > lo_idx[i]:
                maxbuy[i] = tb_p[lo_idx[i]:hi_idx[i]].max()
        maxbuy = maxbuy.reshape(path_ms.shape)
    return {"symbol": vs, "ev_ts": ev_ts, "ev_bid": ev_bid, "ev_mid": ev_mid,
            "p_ask": p_ask, "p_bid": p_bid, "p_mid": p_mid, "maxbuy": maxbuy}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    ap.add_argument("--entry-frac", type=float, default=0.9)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    a = ap.parse_args()

    import numpy as np

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 108)
    print("H74  出库腿：改进最优价（提成交率）vs 少赚价差 —— 修 13% 强平率")
    print("=" * 108)
    print(f"  窗口 {a.hours:.0f}h  ·  入场上限 {a.entry_frac}×半价差  ·  "
          f"maker {a.maker_fee_bp:+.2f}bp  ·  taker {a.taker_fee_bp:+.2f}bp")
    print(f"  强平成本基线（H72 实测）−12.73bp/笔，占 88% 亏损")
    print(f"  成交判据（保守）：真实 ask ≤ 我们的卖价 **且** 该秒有主动买")

    data = {}
    for s in syms:
        d = collect(s, a.hours, a.entry_frac)
        if d:
            data[d["symbol"]] = d
            print(f"  拉取 {d['symbol']:<12} 入场事件 {len(d['ev_ts']):>6,}")
    if not data:
        print("无数据")
        return 1

    FRACS = (0.0, 0.25, 0.5, 0.75, 1.0)
    HOLDS = (30, 60, 90, 120, 180)
    print(f"\n  {'f':>5} {'hold':>6} {'被动成交率':>11} {'每笔期望净额':>13} "
          f"{'标准误':>9} {'CI下界':>10} {'CI上界':>10}")
    print("  " + "-" * 72)
    rows, best = [], None
    for f in FRACS:
        for H in HOLDS:
            nets, hit_any = [], []
            for s, d in data.items():
                p_ask = d["p_ask"][:, :H]; p_bid = d["p_bid"][:, :H]
                p_mid = d["p_mid"][:, :H]; mb_ = d["maxbuy"][:, :H]
                # 我们的卖价 Q = ask − f×(ask−bid)
                Q = p_ask - float(f) * (p_ask - p_bid)
                hit = (p_ask <= Q) & np.isfinite(mb_) & (mb_ >= Q)
                any_hit = hit.any(axis=1)
                first = np.where(any_hit, hit.argmax(axis=1), H - 1)
                idx = np.arange(len(any_hit))
                passive_px = Q[idx, first]
                entry = d["ev_bid"]; m0 = d["ev_mid"]
                net_p = (passive_px - entry) / m0 * 1e4 + a.maker_fee_bp
                forced_px = p_mid[:, H - 1] - (p_ask[:, H - 1] - p_bid[:, H - 1]) / 2.0
                net_f = (forced_px - entry) / m0 * 1e4 - abs(a.taker_fee_bp)
                nets.append(np.where(any_hit, net_p, net_f))
                hit_any.append(any_hit)
            net = np.concatenate(nets)
            ha = np.concatenate(hit_any)
            if len(net) < 100:
                continue
            se = net.std(ddof=1) / np.sqrt(len(net))
            lo, hi = net.mean() - 1.96 * se, net.mean() + 1.96 * se
            rows.append({"f": f, "hold": H, "fill_rate": float(ha.mean()),
                         "mean": float(net.mean()), "se": float(se),
                         "lo": float(lo), "hi": float(hi), "n": int(len(net))})
            if best is None or net.mean() > best["mean"]:
                best = dict(rows[-1])
            print(f"  {f:>5.2f} {H:>5}s {ha.mean()*100:>10.1f}% {net.mean():>+13.4f} "
                  f"{se:>9.4f} {lo:>+10.4f} {hi:>+10.4f}")

    print("\n" + "=" * 108)
    print("判据（事先定死）")
    print("=" * 108)
    if best:
        print(f"\n  最优：f={best['f']:.2f}（卖价 = ask − {best['f']:.2f}×价差）"
              f"  hold={best['hold']}s  被动成交率 {best['fill_rate']*100:.1f}%")
        print(f"    每笔期望净额 {best['mean']:+.4f}bp  "
              f"95%CI [{best['lo']:+.4f}, {best['hi']:+.4f}]  n={best['n']:,}")
        if best["lo"] > 0:
            print("  ⇒ **CI 不含 0，转正** ⇒ 上线 ✓")
        elif best["mean"] > 0:
            print("  ⇒ 点估计转正但 CI 含 0 ⇒ 需更多样本，先小步上线观察")
        else:
            print("  ⇒ **全部组合仍为负** ⇒ 出库腿不是全部问题，需连同入场一起改 ✗")
        # 与实盘对照
        print(f"\n  实盘对照：强平率 13%，每笔 −0.23bp（fill 腿基准）")
        f0 = [r for r in rows if r["f"] == 1.0 and r["hold"] == 60]
        if f0:
            print(f"  当前口径（f=1.0 ≈ 挂 mid，hold=60s）："
                  f"被动成交率 {f0[0]['fill_rate']*100:.1f}%  每笔 {f0[0]['mean']:+.4f}bp")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "entry_frac": a.entry_frac,
                               "rows": rows, "best": best},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H74] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
