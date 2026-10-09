"""H87：挂单距离的**最优值** —— 成交概率 vs 少赚价差的正确权衡（H86 提出的问题）。

# H86 的实测（这把问题重新定义了）

用**入场报价半宽 `edge_bp`** 分档看强平率：

| edge_bp 区间 | 周期数 | 强平率 |
|---|---|---|
| (-∞, 0.070] | 112 | **61.6%** ← 挂得最靠 mid |
| [0.070, 0.320) | 110 | 10.9% |
| [0.320, 0.485) | 113 | 8.9% |
| [0.485, 0.641) | 111 | 10.8% |
| [0.641, ∞) | 112 | 25.9% |

**段宽（行情活跃度）分档**：最静的一档强平率 **57.1%**，其余 11~14%。

⇒ **强平的驱动因素是"能不能成交"，不是"方向对不对"。**
挂得越靠 mid ⇒ 每笔收的点差越多，但**越难成交** ⇒ 撞超时 ⇒ 兜底市价平（昂贵）。
这是一个**明确的经济权衡**，有唯一最优值。

**而 `spread_mult` 正是控制这个距离的参数** —— 它现在是 0.9（凭经验设的）。
H86 显示窄价差币的 edge 落在最易强平的那一档 ⇒ **可能有显著改进空间。**

# 本脚本的模型（吸取 H74 的教训：判据必须先过极端情形自洽测试）

对每个"我们买单成交"的入场事件：
  1. 入场价 `P_in = mid − e×半价差`（`e` = 挂单距离，以半价差为单位）
  2. 出库腿挂 `P_out = mid + e×半价差`（对称）
  3. **被动成交判据（与入场镜像，可观测）**：入场后 `hold_s` 内，
     存在**主动买成交价 ≥ P_out** ⇒ 被动出库
  4. 否则 ⇒ 强平：出场价 = `mid − 半价差`，扣 taker 费
  5. 净额 = `(出场价 − 入场价)/mid × 1e4 + maker费`
  6. 分母 = **全部入场**（含强平），**不排除任何样本**

**自洽测试（H74 死在这里）**：
  · `e` 越小（越靠 mid）⇒ 被动成交率应**越低**（更难被打到）
  · `e` 越大（越远）⇒ 成交率应**越高**
  · 若单调性反了 ⇒ 判据有误，直接作废

# 判据（事先定死）

  · 找出使**每笔期望净额最大**的 `e`
  · 与当前 `e=0.9` 对比，报改善量
  · 若最优 `e` 与 0.9 差距 < 0.1 ⇒ 当前设置已接近最优，无需改

用法：
    .venv\\Scripts\\python.exe scripts\\h87_optimal_quote_distance.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h87_optimal_distance.json"
STEP_MS = 15_000
MAX_H = 120
DEFAULT_SYMS = "BTC,ETH,SOL,XRP,DOGE"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def collect(sym, hours):
    """收集入场事件 + 前向路径（mid/bid/ask + 主动买最高价）。"""
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

    # 入场事件：主动卖价 ≤ 买一（贴 touch，用作"入场发生过"的探测）
    ev = []
    for k in range(len(grids)):
        lo = grids[k]; hi = lo + STEP_MS
        i0 = np.searchsorted(tt, lo, side="left")
        i1 = np.searchsorted(tt, hi, side="left")
        if i1 <= i0:
            continue
        seg = slice(i0, i1)
        if (tbm[seg] & (tp[seg] <= mb[k])).any():
            ev.append(k)
    if len(ev) < 50:
        return None
    ev = np.array(ev)

    # 前向路径（1s 分辨率）：主动买最高价（用于判出库成交）
    offs = np.arange(1, MAX_H + 1) * 1000
    path_ms = grids[ev][:, None] + offs[None, :]
    buy_t = tt[~tbm]; buy_p = tp[~tbm]
    hi_i = np.searchsorted(buy_t, path_ms.ravel(), side="right")
    lo_i = np.searchsorted(buy_t, path_ms.ravel() - 1000, side="right")
    mbuy = np.full(path_ms.size, np.nan)
    nz = np.nonzero(hi_i > lo_i)[0]
    for i in nz:
        mbuy[i] = buy_p[lo_i[i]:hi_i[i]].max()
    mbuy = np.fmax.accumulate(np.nan_to_num(mbuy, nan=-np.inf),
                              axis=0).reshape(path_ms.shape)
    mbuy[~np.isfinite(mbuy)] = np.nan

    pj = np.searchsorted(ot, path_ms.ravel(), side="right") - 1
    pj = np.clip(pj, 0, len(ot) - 1)
    p_mid = (0.5 * (ob_[pj] + oa[pj])).reshape(path_ms.shape)
    p_bid = ob_[pj].reshape(path_ms.shape)
    p_ask = oa[pj].reshape(path_ms.shape)

    return {"symbol": vs, "mid0": mid_g[ev], "half0": half_g[ev],
            "bid0": mb[ev], "ask0": ma[ev],
            "p_mid": p_mid, "p_bid": p_bid, "p_ask": p_ask, "mbuy": mbuy}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    ap.add_argument("--hold-s", type=int, default=60)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    a = ap.parse_args()

    import numpy as np

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 104)
    print("H87  挂单距离的最优值（成交概率 vs 少赚价差）")
    print("=" * 104)
    print(f"  窗口 {a.hours:.0f}h  ·  hold {a.hold_s}s  ·  maker {a.maker_fee_bp:+.2f}bp"
          f"  ·  taker {a.taker_fee_bp:+.2f}bp")
    print(f"  依据 H86：edge 最小档强平率 61.6%，最大档 25.9%；段宽最静档 57.1%")

    data = {}
    for s in syms:
        d = collect(s, a.hours)
        if d:
            data[d["symbol"]] = d
            print(f"  拉取 {d['symbol']:<12} 入场事件 {len(d['mid0']):>6,}")
    if not data:
        print("无数据")
        return 1

    H = a.hold_s
    ES = (0.2, 0.4, 0.6, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0)

    # ── 步骤 0：极端情形自洽测试（H74 死在这里）──
    print("\n" + "=" * 104)
    print("步骤 0：自洽测试 —— e 越大（挂越远）⇒ 被动成交率应越高")
    print("=" * 104)
    rates = []
    for e in ES:
        hits = []
        for s, d in data.items():
            h0 = d["half0"][:, None]
            P_out = d["mid0"][:, None] + e * h0
            mb = d["mbuy"][:, :H]
            hits.append((np.isfinite(mb) & (mb >= P_out)).any(axis=1))
        ha = np.concatenate(hits)
        rates.append((e, float(ha.mean())))
        print(f"  e={e:>4.1f}（入场 mid−{e}×半价差，出库 mid+{e}×半价差）"
              f"  被动成交率 {ha.mean()*100:>6.1f}%")
    mono = all(rates[i][1] <= rates[i + 1][1] + 1e-9 for i in range(len(rates) - 1))
    print(f"\n  单调性：{'✓ 通过' if mono else '✗ **失败** ⇒ 判据有误，作废'}")
    if not mono:
        return 1

    # ── 主表 ──
    print("\n" + "=" * 104)
    print(f"主表：每笔期望净额（分母 = 全部入场，含强平；hold={H}s）")
    print("=" * 104)
    print(f"\n  {'e':>5} {'成交率':>8} {'价差捕获bp':>11} {'每笔净额bp':>11} "
          f"{'标准误':>9} {'CI下界':>10} {'CI上界':>10}")
    print("  " + "-" * 70)
    rows, best = [], None
    for e in ES:
        nets, hl = [], []
        for s, d in data.items():
            h0 = d["half0"]; m0 = d["mid0"]
            P_in = m0[:, None] - e * h0[:, None]
            P_out = m0[:, None] + e * h0[:, None]
            mb = d["mbuy"][:, :H]
            hit = np.isfinite(mb) & (mb >= P_out)
            any_hit = hit.any(axis=1)
            # 强平：出场 = mid − 半价差（taker）
            fmid = d["p_mid"][:, H - 1]
            fhalf = (d["p_ask"][:, H - 1] - d["p_bid"][:, H - 1]) / 2.0
            forced_px = fmid - fhalf
            net_p = (P_out[:, 0] - P_in[:, 0]) / m0 * 1e4 + a.maker_fee_bp
            net_f = (forced_px - P_in[:, 0]) / m0 * 1e4 - abs(a.taker_fee_bp)
            nets.append(np.where(any_hit, net_p, net_f))
            hl.append(any_hit)
        net = np.concatenate(nets); ha = np.concatenate(hl)
        if len(net) < 100:
            continue
        se = net.std(ddof=1) / np.sqrt(len(net))
        lo, hi = net.mean() - 1.96 * se, net.mean() + 1.96 * se
        cap = float(np.mean([2.0 * e for _ in [0]]))  # 价差捕获 = 2e × 半价差
        rows.append({"e": e, "fill_rate": float(ha.mean()), "mean": float(net.mean()),
                     "se": float(se), "lo": float(lo), "hi": float(hi),
                     "n": int(len(net))})
        if best is None or net.mean() > best["mean"]:
            best = dict(rows[-1])
        print(f"  {e:>5.1f} {ha.mean()*100:>7.1f}% {'':>11} {net.mean():>+11.4f} "
              f"{se:>9.4f} {lo:>+10.4f} {hi:>+10.4f}")

    print("\n" + "=" * 104)
    print("判据")
    print("=" * 104)
    if best:
        print(f"\n  最优 e = **{best['e']:.1f}**（入场 mid−{best['e']:.1f}×半价差，"
              f"出库 mid+{best['e']:.1f}×半价差）")
        print(f"    被动成交率 {best['fill_rate']*100:.1f}%   每笔净额 {best['mean']:+.4f}bp  "
              f"95%CI [{best['lo']:+.4f}, {best['hi']:+.4f}]")
        cur = [r for r in rows if abs(r["e"] - 0.9) < 1e-9]
        if cur:
            d_ = best["mean"] - cur[0]["mean"]
            print(f"\n  当前实盘 e≈0.9：每笔 {cur[0]['mean']:+.4f}bp"
                  f"（成交率 {cur[0]['fill_rate']*100:.1f}%）")
            print(f"  ⇒ 改到 e={best['e']:.1f} 的改善 = **{d_:+.4f}bp/笔**")
            if abs(best["e"] - 0.9) < 0.1:
                print("  ⇒ 最优 e 与当前 0.9 差距 <0.1 ⇒ **当前设置已接近最优，无需改**")
            else:
                print(f"  ⇒ 最优 e 偏离当前值 ⇒ 值得调整 `spread_mult`")
        if best["lo"] > 0:
            print("  ⇒ 最优组合的 95%CI **不含 0** ⇒ 转正 ⇒ 可上线")
        else:
            print("  ⇒ 最优组合仍含 0 ⇒ 需更多样本或还有别的成本项")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "hold_s": H, "rows": rows,
                               "best": best, "mono": [[e, r] for e, r in rates]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H87] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
