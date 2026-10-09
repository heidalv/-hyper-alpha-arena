"""H77：出库规则的正确测量 —— 强平超时 vs 被动挂价（判据已修正）。

# H74 为什么作废

H74 的成交判据 `真实 ask ≤ Q`（`Q = ask − f×价差`）在 `f>0` 时**恒假**
⇒ 输出"越激进越不成交"的荒谬结果。**判据本身要先过极端情形自洽测试**（第 33 条教训）。

# 本脚本的正确判据（与入场侧同构、镜像）

入场侧一直用：**主动卖价 ≤ 我们的买价 ⇒ 成交**（我们是最优买价）。
镜像到出库侧：**主动买价 ≥ 我们的卖价 ⇒ 成交**。
两个方向用同一套物理规则，不存在"单向假设"。

# 测什么

对每笔入场（我们买到）：
  1. 挂出库卖单，价格 `Q = ask − f × (ask−bid)`（f=0 挂 best_ask，f=1 挂 mid）
  2. 在 `hold_s` 内，若出现 **主动买成交价 ≥ Q** ⇒ **被动出库**，
     净额 = `(Q − 入场价)/mid × 1e4 + maker费`
  3. 否则在 `hold_s` 末**强平**：出场价 = `mid − 半价差`，扣 **taker 费**
  4. 分母 = **全部入场**（含强平），**不做样本排除**

# 与 H74 的关键差别

**不比较 `ask` 与 `Q`**（那引入了 "f" 作为开关的伪依赖），
而是比较**真实主动买成交价**与 `Q` —— 这是可观测事件，不依赖队列位置假设。

# 判据（事先定死）

  · 先做**极端情形自洽测试**：f=0（最保守要价）的成交率应**低于** f=1（最激进要价）
  · 若不自洽 ⇒ 判据仍有问题，直接作废（不解释、不救）
  · 自洽后：找出使每笔期望净额最大的 (f, hold_s)，看 95%CI 是否不含 0

用法：
    .venv\\Scripts\\python.exe scripts\\h77_exit_rule_correct.py --hours 48
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

OUT = ROOT / "research_l1" / "out" / "h77_exit_rule.json"
STEP_MS = 15_000
MAX_H = 240
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
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]

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

    # 入场事件：主动卖价 ≤ 我们买价
    ev = []
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + STEP_MS
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg = slice(i0, i1)
        if (tbm[seg] & (tp[seg] <= px_bid[k])).any():
            ev.append(k)
    if len(ev) < 50:
        return None
    ev = np.array(ev)
    ev_ts = grids[ev]
    ev_bid = px_bid[ev]
    ev_mid = mid_g[ev]
    ev_ask = ma[ev]
    ev_bidpx = mb[ev]

    # 前向：每个 1s 点上，**主动买成交价的最高值**（用于判"能否打掉我们的卖单"）
    offs = np.arange(1, MAX_H + 1) * 1000
    path_ms = ev_ts[:, None] + offs[None, :]
    # 在 (t-1000, t] 窗口内的主动买最高价
    buy_t = tt[~tbm]; buy_p = tp[~tbm]
    hi_i = np.searchsorted(buy_t, path_ms.ravel(), side="right")
    lo_i = np.searchsorted(buy_t, path_ms.ravel() - 1000, side="right")
    has = hi_i > lo_i
    # 向量化的窗口最大值：用 1s 网格上的 max 再做前缀最大
    # 简化：按 1s 分桶取 max，然后前缀最大（保证覆盖整段）
    bucket = (path_ms.ravel() // 1000)
    mx = np.full(path_ms.size, np.nan)
    ii = np.nonzero(has)[0]
    if len(ii) < path_ms.size:
        # 逐点取窗口最大（点数多但只做一次，用 numpy 分段）
        for i in ii:
            mx[i] = buy_p[lo_i[i]:hi_i[i]].max()
    maxbuy = mx.reshape(path_ms.shape)
    # 出库判据用**累计**最高主动买价：一旦达到就不再回落（保守偏好"能成交"）
    maxbuy = np.fmax.accumulate(np.nan_to_num(maxbuy, nan=-np.inf), axis=1)
    maxbuy[~np.isfinite(maxbuy)] = np.nan

    # 路径上的 mid / bid / ask
    pj = np.searchsorted(ot, path_ms.ravel(), side="right") - 1
    pj = np.clip(pj, 0, len(ot) - 1)
    p_mid = (0.5 * (ob_[pj] + oa[pj])).reshape(path_ms.shape)
    p_bid = ob_[pj].reshape(path_ms.shape)
    p_ask = oa[pj].reshape(path_ms.shape)
    return {"symbol": vs, "ev_mid": ev_mid, "ev_bid": ev_bid,
            "ev_ask": ev_ask, "ev_bidpx": ev_bidpx,
            "maxbuy": maxbuy, "p_mid": p_mid, "p_bid": p_bid, "p_ask": p_ask}


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
    print("=" * 106)
    print("H77  出库规则（判据修正版）：强平超时 vs 被动挂价")
    print("=" * 106)
    print(f"  窗口 {a.hours:.0f}h  ·  入场 {a.entry_frac}×半价差  ·  "
          f"maker {a.maker_fee_bp:+.2f}bp  ·  taker {a.taker_fee_bp:+.2f}bp")
    print(f"  成交判据（与入场镜像）：**主动买成交价 ≥ 我们卖价** ⇒ 成交")

    data = {}
    for s in syms:
        d = collect(s, a.hours, a.entry_frac)
        if d:
            data[d["symbol"]] = d
            print(f"  拉取 {d['symbol']:<12} 入场事件 {len(d['ev_mid']):>6,}")
    if not data:
        print("无数据")
        return 1

    FRACS = (0.0, 0.25, 0.5, 0.75, 1.0)
    HOLDS = (30, 60, 90, 120, 180, 240)

    # ── 步骤 0：极端情形自洽测试（H74 就是死在这里）──
    print("\n" + "=" * 106)
    print("步骤 0：极端情形自洽测试 —— 成交率必须随 f 单调不减")
    print("=" * 106)
    rates = []
    for f in FRACS:
        hit_any = []
        for s, d in data.items():
            mbuy = d["maxbuy"][:, :60]
            Q = d["p_ask"][:, :60] - float(f) * (d["p_ask"][:, :60] - d["p_bid"][:, :60])
            # 注意：这里 Q 用**入场时刻**的价差定一次，之后固定（挂单不随行情改价）
            Q = d["ev_ask"][:, None] - float(f) * (d["ev_ask"][:, None] - d["ev_bidpx"][:, None])
            hit_any.append((np.isfinite(mbuy) & (mbuy >= Q)).any(axis=1))
        ha = np.concatenate(hit_any)
        rates.append((f, float(ha.mean())))
        print(f"  f={f:>4.2f}（卖价 = ask − {f:.2f}×价差）  60s 内被动成交率 {ha.mean()*100:>6.1f}%")
    mono = all(rates[i][1] <= rates[i + 1][1] + 1e-9 for i in range(len(rates) - 1))
    print(f"\n  单调性：{'✓ 通过' if mono else '✗ **失败** ⇒ 判据仍有问题，直接作废'}")
    if not mono:
        print("  （不解释、不救 —— 第 33 条教训：判据先过极端情形自洽测试）")
        return 1

    # ── 主表 ──
    print("\n" + "=" * 106)
    print("主表：每笔期望净额（分母 = 全部入场，含强平）")
    print("=" * 106)
    print(f"\n  {'f':>5} {'hold':>6} {'被动成交率':>10} {'每笔净额':>11} {'标准误':>9} "
          f"{'CI下界':>10} {'CI上界':>10}")
    print("  " + "-" * 70)
    rows, best = [], None
    for f in FRACS:
        for H in HOLDS:
            nets, hits = [], []
            for s, d in data.items():
                mbuy = d["maxbuy"][:, :H]
                Q = (d["ev_ask"][:, None]
                     - float(f) * (d["ev_ask"][:, None] - d["ev_bidpx"][:, None]))
                hit = np.isfinite(mbuy) & (mbuy >= Q)
                any_hit = hit.any(axis=1)
                first = np.where(any_hit, hit.argmax(axis=1), H - 1)
                idx = np.arange(len(any_hit))
                passive_px = Q[idx, 0]          # 挂价固定 = 入场时刻定的 Q
                forced_px = d["p_mid"][:, H - 1] - (d["p_ask"][:, H - 1]
                                                    - d["p_bid"][:, H - 1]) / 2.0
                m0 = d["ev_mid"]; entry = d["ev_bid"]
                net_p = (passive_px - entry) / m0 * 1e4 + a.maker_fee_bp
                net_f = (forced_px - entry) / m0 * 1e4 - abs(a.taker_fee_bp)
                nets.append(np.where(any_hit, net_p, net_f))
                hits.append(any_hit)
            net = np.concatenate(nets); ha = np.concatenate(hits)
            if len(net) < 100:
                continue
            se = net.std(ddof=1) / np.sqrt(len(net))
            lo, hi = net.mean() - 1.96 * se, net.mean() + 1.96 * se
            rows.append({"f": f, "hold": H, "fill_rate": float(ha.mean()),
                         "mean": float(net.mean()), "se": float(se),
                         "lo": float(lo), "hi": float(hi), "n": int(len(net))})
            if best is None or net.mean() > best["mean"]:
                best = dict(rows[-1])
            print(f"  {f:>5.2f} {H:>5}s {ha.mean()*100:>9.1f}% {net.mean():>+11.4f} "
                  f"{se:>9.4f} {lo:>+10.4f} {hi:>+10.4f}")

    print("\n" + "=" * 106)
    print("判据")
    print("=" * 106)
    if best:
        print(f"\n  最优：f={best['f']:.2f}  hold={best['hold']}s  "
              f"被动成交率 {best['fill_rate']*100:.1f}%")
        print(f"    每笔期望净额 {best['mean']:+.4f}bp  95%CI "
              f"[{best['lo']:+.4f}, {best['hi']:+.4f}]  n={best['n']:,}")
        if best["lo"] > 0:
            print("  ⇒ **CI 不含 0 ⇒ 转正** ⇒ 可上线")
        elif best["mean"] > 0:
            print("  ⇒ 点估计转正、CI 含 0 ⇒ 需更多样本")
        else:
            print("  ⇒ 全部组合仍为负")
        cur = [r for r in rows if r["f"] == 0.0 and r["hold"] == 60]
        if cur:
            print(f"\n  当前实盘口径近似（f=0 挂 best_ask / hold=60s）："
                  f"成交率 {cur[0]['fill_rate']*100:.1f}%  每笔 {cur[0]['mean']:+.4f}bp")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "rows": rows, "best": best,
                               "monotone_check": [[f, r] for f, r in rates]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H77] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
