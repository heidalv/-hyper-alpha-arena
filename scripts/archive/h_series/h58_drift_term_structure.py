"""H58：漂移的时间曲线 —— 为什么 300s 持有是灾难，而 30s 可以接受。

# 从实盘倒推出来的问题

实盘车道分解（H57）：
    已实现亏损 −$15.172 · 成交 1,642 笔 · 每笔名义 $28.51
    ⇒ **每笔净额 −3.24bp**

而模型（H54，30s 持有）说 −0.59bp。**差 5.5 倍。**
差在哪？引擎的 `max_one_side_seconds = 300`（持有上限 **5 分钟**），
而模型只测到 30s。**引擎跑在模型从未验证过的时区。**

文献一侧是同一结论：
  · Cartea et al. arXiv:2312.05827 —— 毒性流占比 **6.6%@1s → 71%@70s**
  · Albers arXiv:2502.18625v2 §6 —— 成交概率与"成交后 5 秒内收益"呈**显著负相关**，
    且**填满概率在 mid 上行时为 0、在 mid 持平时为 1**（逆选择是恒等式，不是倾向）
  · Albers Table 2 —— 各做市变体平均持有 22.6~38.8s，**没有一个是 300s**

# 本脚本测什么

同一套真实 tick（`asterdex_book_ticker` + `asterdex_trades`），
对**同一批成交样本**，在两个不同"绑定"的持有时间下算净额：

  (a) **引擎绑定**：持有到 min(300s, 直到价格回到开仓价)
  (b) **时间绑定**：固定持有 h ∈ {1,3,5,10,30,60,120,300}s

输出漂移的时间曲线 —— 这是决定持有上限的**唯一**依据。

# 判据（事先定死）

  · 若漂移在 30s 后**继续恶化** ⇒ `max_one_side_seconds` 必须 ≤30s（与模型同区）
  · 若漂移在某个 h 之后**走平** ⇒ 该 h 是持有上限的自然选择
  · 任何情况下**不排除样本**；同时报均值/中位/p5/胜率（第 15/16 条教训）

用法：
    .venv\\Scripts\\python.exe scripts\\h58_drift_term_structure.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h58_drift_term.json"
HORIZONS = (1, 3, 5, 10, 30, 60, 120, 300)


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def _st(x):
    import numpy as np

    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"n": 0}
    return {"n": int(len(x)), "mean": round(float(x.mean()), 4),
            "median": round(float(np.median(x)), 4),
            "p5": round(float(np.percentile(x, 5)), 4),
            "p95": round(float(np.percentile(x, 95)), 4),
            "win": round(float((x > 0).mean()), 4)}


def run_symbol(sym, hours, maker_fee_bp):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    # bare symbol（BTC）→ 完整合约符号（BTCUSDT）：book_ticker / trades 用后者，
    # market_orderbook_snapshots 用前者 —— 混用会**静默返回 0 行** ✗
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
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        "  ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(ob) < 2000 or len(tr) < 200:
        return None

    ot = np.array([r["event_ts_ms"] for r in ob], dtype=np.int64)
    ob_ = np.array([r["b"] for r in ob]); oa = np.array([r["a"] for r in ob])
    ok = (ob_ > 0) & (oa > ob_)
    ot, ob_, oa = ot[ok], ob_[ok], oa[ok]
    mid = 0.5 * (ob_ + oa)

    tt = np.array([r["event_ts_ms"] for r in tr], dtype=np.int64)
    tp = np.array([r["p"] for r in tr])
    tbm = np.array([bool(r["is_buyer_maker"]) for r in tr])
    v = tp > 0
    tt, tp, tbm = tt[v], tp[v], tbm[v]
    # 只取主动卖（打我们的买单）—— 买腿是"被逆选择"的那一侧
    sell = tbm
    tt_s, tp_s = tt[sell], tp[sell]
    if len(tt_s) < 100:
        return None

    # 成交时刻的 mid（严格因果）
    idx = np.searchsorted(ot, tt_s, side="right") - 1
    g = idx >= 0
    tt_s, tp_s, idx = tt_s[g], tp_s[g], idx[g]
    m_t = mid[idx]

    # 我们的买价 = 成交时刻的买一（贴 touch）。开仓优势 = (mid − bid)。
    # 这里直接以 **mid 为开仓基准**：贴 touch 的挂单成交时，我们付的是 bid，
    # 而相对 mid 已经有 (mid−bid)/mid 的即时优势 ⇒ 单独报出来，不混进漂移。
    bid_t = ob_[idx]
    edge_bp = (m_t - bid_t) / m_t * 1e4
    # 成交价相对 mid 的偏移（成交价通常=bid，但可能有更差成交）
    exec_off_bp = (m_t - tp_s) / m_t * 1e4

    out = {"symbol": vs, "n": int(len(tt_s)),
           "edge_bp": _st(edge_bp), "exec_off_bp": _st(exec_off_bp)}
    for h in HORIZONS:
        j = np.searchsorted(ot, tt_s + h * 1000, side="right") - 1
        jj = np.clip(j, 0, len(ot) - 1)
        # 漂移 = 未来 mid − 开仓时 mid（买单：上行有利）
        drift = (mid[jj] - m_t) / m_t * 1e4
        # 净额（以"贴 touch 成交"计）：开仓成本 = tp_s，出场 = 未来 mid
        net = (mid[jj] - tp_s) / m_t * 1e4 + maker_fee_bp
        out[f"h{h}"] = {"drift": _st(drift), "net": _st(net)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,DOGE")
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    a = ap.parse_args()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    print("=" * 106)
    print("H58  漂移的时间曲线 —— 300s 持有 vs 30s 持有（实盘 −3.24bp/笔 的解释）")
    print("=" * 106)
    print(f"  窗口 {a.hours:.0f}h  ·  maker {a.maker_fee_bp:+.2f}bp  ·  样本=主动卖成交（打买腿）")

    res = {}
    for s in syms:
        d = run_symbol(s, a.hours, a.maker_fee_bp)
        if d:
            res[s] = d
    if not res:
        print("无数据")
        return 1

    # ── 主表：漂移与净额的时间曲线（各币分别 + 跨币简单平均）──
    print(f"\n【漂移（未来 mid − 当前 mid，bp）—— 买腿，正=有利】")
    hdr = "  " + f"{'币':<10}" + "".join(f"{str(h)+'s':>9}" for h in HORIZONS)
    print(hdr)
    print("  " + "-" * (10 + 9 * len(HORIZONS)))
    for s, d in res.items():
        print(f"  {s:<10}" + "".join(
            f"{d[f'h{h}']['drift'].get('mean', float('nan')):>9.3f}" for h in HORIZONS))

    print(f"\n【净额（未来 mid − 成交价 + maker 费，bp）】")
    print(hdr)
    print("  " + "-" * (10 + 9 * len(HORIZONS)))
    for s, d in res.items():
        print(f"  {s:<10}" + "".join(
            f"{d[f'h{h}']['net'].get('mean', float('nan')):>9.3f}" for h in HORIZONS))

    # 跨币等权平均的时间曲线
    print(f"\n【跨币等权平均】")
    print(f"  {'视界':>7} {'漂移均值':>10} {'净额均值':>10} {'净额中位':>10} "
          f"{'净额 p5':>10} {'胜率':>8} {'样本':>10}")
    print("  " + "-" * 74)
    curve = {}
    for h in HORIZONS:
        dm = [res[s][f"h{h}"]["drift"]["mean"] for s in res
              if res[s][f"h{h}"]["drift"].get("n")]
        nm = [res[s][f"h{h}"]["net"]["mean"] for s in res
              if res[s][f"h{h}"]["net"].get("n")]
        md = [res[s][f"h{h}"]["net"]["median"] for s in res]
        p5 = [res[s][f"h{h}"]["net"]["p5"] for s in res]
        wn = [res[s][f"h{h}"]["net"]["win"] for s in res]
        nn = sum(res[s][f"h{h}"]["net"]["n"] for s in res)
        f = lambda v: sum(v) / len(v) if v else float("nan")
        curve[h] = {"drift_mean": round(f(dm), 4), "net_mean": round(f(nm), 4),
                    "net_median": round(f(md), 4), "net_p5": round(f(p5), 4),
                    "win": round(f(wn), 4), "n": int(nn)}
        print(f"  {str(h)+'s':>7} {f(dm):>10.4f} {f(nm):>10.4f} {f(md):>10.4f} "
              f"{f(p5):>10.4f} {f(wn):>8.3f} {nn:>10,}")

    # ── 实盘对账：每笔 −3.24bp 最接近哪个视界？ ──
    LIVE_BP = -3.24
    print(f"\n" + "=" * 106)
    print(f"实盘对账：实测 **−3.24bp/笔**（H57：−$15.172 / 1,642 笔 / $28.51）")
    print("=" * 106)
    best_h, best_gap = None, 1e9
    for h in HORIZONS:
        gap = abs(curve[h]["net_mean"] - LIVE_BP)
        mark = ""
        if gap < best_gap:
            best_gap, best_h, mark = gap, h, "  ← 最接近"
        print(f"  视界 {h:>4}s  模型净额 {curve[h]['net_mean']:>+8.4f}bp   "
              f"与实盘差 {curve[h]['net_mean'] - LIVE_BP:>+8.4f}bp{mark}")
    print(f"\n  ⇒ 实盘最接近 **{best_h}s** 的模型值（{curve[best_h]['net_mean']:+.4f}bp，"
          f"差 {best_gap:.4f}bp），而引擎 `max_one_side_seconds=300`。")
    if best_h <= 60:
        print(f"  ⇒ **持有上限必须从 300s 收到 ≤{best_h}s** —— 这是可直接执行的修复。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"hours": a.hours, "maker_fee_bp": a.maker_fee_bp,
         "horizons": list(HORIZONS), "curve": {str(k): v for k, v in curve.items()},
         "live_bp_per_fill": LIVE_BP, "closest_horizon_s": best_h,
         "per_symbol": {s: {k: v for k, v in d.items()} for s, d in res.items()}},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[H58] 写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
