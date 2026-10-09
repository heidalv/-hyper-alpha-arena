"""H104：出库报价挂到多近才**真能成交**？—— 把 H103 的"本可离场"变成可执行参数。

# H103 的关键纠正

我曾断言"剩下 ~10% 的强平是地板 —— 那些仓位等不到被动成交"。
**实测推翻了这个断言**（120 个含强平周期，300s 真实 mid 路径）：

```
mid 曾回到「入场价」（可不亏离场）：**86.7%**
mid 曾到「入场价 + 0.5bp」（小赚）：  83.3%
mid 从未回到入场价：              13.3%
回到入场价的 tick 数中位：        **2,852**
```

**⇒ 87% 的强平仓位**其实都回到过可离场的位置**，而且停留了很久。
**⇒ 出库腿没成交不是因为"市场不回来"，而是因为**我们的报价没被吃到**。**

# 本脚本测什么（决定 spread_mult_reduce 该设多少）

出库是"卖" ⇒ 成交条件是**主动买成交价 ≥ 我们的卖价 Q**。
所以我们直接测：对这些"本可离场"的仓位，
**在 300s 内主动买成交价的最高值**分布如何，
以及若把 Q 挂在不同水平，能覆盖多少比例。

`Q = mid_entry + f × half_spread`，f 从 0（挂 mid）到 1.0（挂卖一）到 >1（越过卖一**不行**，会变 taker）。

**⇒ f 的合法上限是"刚好在卖一内侧"**，而由于卖一会移动，
这里用**入场时刻的半价差**做基准，并同时报"有多少比例会越过当时的卖一"（越界率）。

判据（事先定死）：
  · 找出使"被动成交率"显著提升且越界率 ≈0 的 f
  · 与当前 `spread_mult_reduce = 0.95` 对比
  · 若最优 f 与 0.95 差距大 ⇒ 调整该参数并 A/B

用法：
    .venv\\Scripts\\python.exe scripts\\h104_exit_quote_distance.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

H = 300
MAX_EP = 120
FS = (0.0, 0.5, 0.95, 1.0, 1.2, 1.5, 2.0)


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h84_derive_episodes import derive, load

    print("=" * 100)
    print("H104  出库报价挂到多近才真能成交？（含强平仓位，300s 真实路径）")
    print("=" * 100)

    rows = load()
    eps = derive(rows)
    flat = [e for e in eps if e["flat"]]
    by_key = {}
    for r in rows:
        by_key.setdefault((r.get("symbol"), r.get("ts")), r)
    print(f"\n  含强平周期 {len(flat)} / 总周期 {len(eps)}；最多检查 {MAX_EP} 个")

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 每个样本：入场价、入场时半价差、以及 300s 内主动买最高价
    samples = []
    n_noob = n_notrade = 0
    for e in flat[:MAX_EP]:
        r0 = by_key.get((e["sym"], e.get("ts0")))
        if r0 is None:
            continue
        px = float(r0.get("fill_px") or 0)
        ts0 = float(r0.get("ts") or 0)
        if px <= 0 or ts0 <= 0:
            continue
        vs = e["sym"] if str(e["sym"]).endswith("USDT") else f"{e['sym']}USDT"
        lo, hi = int(ts0 * 1000), int((ts0 + H) * 1000)
        try:
            cur.execute(
                "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
                "  FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
                "   AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms",
                (vs, lo, hi))
            d = cur.fetchall()
            cur.execute(
                "SELECT price::float p, is_buyer_maker"
                "  FROM asterdex_trades"
                " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
                " ORDER BY event_ts_ms", (vs, lo, hi))
            tr = cur.fetchall()
        except Exception:
            n_noob += 1
            continue
        if len(d) < 5:
            n_noob += 1
            continue
        mid = np.array([0.5 * (x["b"] + x["a"]) for x in d])
        asks = np.array([x["a"] for x in d])
        # 入场时半价差：用入场时刻的 mid 与入场价反推
        # edge_bp = (mid−px)/mid×1e4（买单）；这里直接用 |mid[0] − px|
        half = abs(mid[0] - px)
        if half <= 0:
            half = (asks[0] - mid[0]) if asks[0] > mid[0] else abs(mid[0]) * 1e-6
        # 主动买最高价
        buyp = [float(x["p"]) for x in tr if not bool(x["is_buyer_maker"])]
        if not buyp:
            n_notrade += 1
            samples.append({"px": px, "mid0": mid[0], "half": half,
                            "maxbuy": np.nan, "ask_max": float(asks.max()),
                            "mid_max": float(mid.max())})
            continue
        samples.append({"px": px, "mid0": mid[0], "half": half,
                        "maxbuy": float(max(buyp)),
                        "ask_max": float(asks.max()),
                        "mid_max": float(mid.max())})
    cn.close()
    print(f"\n  诊断：盘口不足 {n_noob}   窗口内无主动买 {n_notrade}   "
          f"⇒ 可用样本 **{len(samples)}**")
    if not samples:
        return 0

    px = np.array([s["px"] for s in samples])
    mid0 = np.array([s["mid0"] for s in samples])
    half = np.array([s["half"] for s in samples])
    mb = np.array([s["maxbuy"] for s in samples])

    print("\n" + "=" * 100)
    print("各 f 下：出库报价 Q = px + f×half（卖出方向）⇒ 成交条件 maxbuy ≥ Q")
    print("=" * 100)
    print(f"\n  {'f':>6} {'Q相对入场价bp':>14} {'被动成交率':>10} "
          f"{'越过当时卖一的比例':>18} {'说明':<18}")
    print("  " + "-" * 74)
    ok_ = np.isfinite(mb)
    for f in FS:
        Q = px + f * half
        hit = ok_ & (mb >= Q)
        rate = hit.sum() / max(ok_.sum(), 1)
        # 越界：Q 超过了 300s 内出现过的最高卖一 ⇒ 那会是 taker（近似）
        cross = (Q > np.array([s["ask_max"] for s in samples])).mean()
        note = ""
        if f < 0.01:
            note = "挂 mid"
        elif abs(f - 0.95) < 1e-9:
            note = "**当前值**"
        elif abs(f - 1.0) < 1e-9:
            note = "挂到入场时卖一"
        else:
            note = "越过入场时卖一" if f > 1.0 else "价差内"
        print(f"  {f:>6.2f} {f*half.mean()/mid0.mean()*1e4:>14.3f} "
              f"{rate*100:>9.1f}% {cross*100:>17.1f}% {note:<18}")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    rates = {}
    for f in FS:
        Q = px + f * half
        rates[f] = float((ok_ & (mb >= Q)).sum() / max(ok_.sum(), 1))
    cur_r = rates.get(0.95, float("nan"))
    best_f = max(rates, key=lambda k: rates[k])
    print(f"\n  当前 spread_mult_reduce=0.95 ⇒ 被动成交率 **{cur_r*100:.1f}%**")
    print(f"  最优 f = {best_f:.2f} ⇒ 被动成交率 **{rates[best_f]*100:.1f}%**"
          f"（提升 {(rates[best_f]-cur_r)*100:+.1f} pp）")
    if best_f > 0.95 + 1e-9 and rates[best_f] - cur_r > 0.10:
        print(f"  ⇒ 值得把出库挂得更靠外（f 0.95 → {best_f:.2f}）")
        print(f"     ⇒ 即**牺牲部分价差换成交率** —— 正是 H102 说的「第三种出库机制」")
    else:
        print(f"  ⇒ 当前 0.95 已接近最优，无需调整")
    print(f"\n  ⚠️ '被动成交率'是**上界**：假设我们排到队首且主动买一定吃到我们。")
    print(f"     真实成交率会更低，但**不同 f 之间的相对排序**仍然可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
