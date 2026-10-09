"""H19：用论文的**队列消耗制**成交规则，测真实的成交概率与 markout。

## 为什么必须做这个（这是本轮最重要的未测量项）

我们现在的成交模型（`backend/services/market_maker/core.py::fill_side`）在两个方向上都错了：

  触发条件 `seg_low < bid AND seg_taker_sell > 0`，且 `MM_F60_PENETRATION_BP=0`
  —— 它自己的 docstring 写着"等价于假设我们排在队列最前面"。

  ① **太严格**：要求价格**穿过**我们的档位。论文第 612–618 行指出，
     触碰档位的成交只需要「自挂单以来累计对手方主动量 > 前面挂量 LA」，
     **价格不必穿过**。⇒ 我们漏掉了大量真实会成交的样本。
  ② **太宽松**：同时假设 LA=0（队首）。⇒ 我们多算了本该排在队尾的成交。

后果：我们统计到的成交样本恰好是**机械上最有毒的子集**（价格穿过去了才记），
所以账本里那个 −2.02bp 的"逆选择"是在**被截断且逆向选择过的样本**上测的。

论文 Table 1 显示，**同一格内队首 vs 队尾的 markout 差 0.12–0.86bp —— 比我们整个
净边际（0.63bp）还大。**

## 本脚本做什么

用 **tick 级**真实数据（`asterdex_trades` 逐笔 + `asterdex_book_ticker` 最优档挂量）
实现论文的成交规则，并给出 16 格 markout 表所需的核心量：

    对每个 tick t，模拟"在最优买价挂一张被动买单"：
      · 队列前方量 LA = 挂单时刻最优档挂量 × 队列位置系数
        （1.0 = 排在最后 = **保守且可实现的假设**；0.0 = 队首 = 我们现在的假设）
      · 成交条件：自挂单以来，**在该价位的累计主动卖量 ≥ LA**
        （不看价格是否穿过）
      · markout：从**限价**算到成交后 Δ 时刻的中价，bp
        （与论文 Table 1 口径一致：`sgn·(mid_{T+Δ}/p_fill − 1)·1e4`）

同时用**我们现在的穿过制规则**跑同一批报价，对比：
      · 成交笔数比
      · markout 均值差
      · **配对差**（两种规则都判成交的那些报价上的差）

## 判据（事先定好）

  · 若队列制比穿过制**多很多成交**且 markout **更不毒** ⇒ 我们的账本系统性偏毒，
    所有基于账本的结论（含"逆选择 −2.02bp"）都需要重测。
  · 若队列制成交更多但 markout **更毒** ⇒ 深度毒性机制成立，"我们只是少成交了"这个
    假设被否掉。
  · 若队列位置系数从 1.0 变到 0.0，markout 改善 **< 0.1bp** ⇒ 队列位置在我们的场地
    不重要（周转太快），论文的 Table 1 杠杆不可移植。

用法：
    .venv\\Scripts\\python.exe scripts\\h19_queue_fill_model.py --hours 12
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

OUT_DIR = ROOT / "research_l1" / "out"
MARKOUT_MS = [1000, 5000, 30000, 300000]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "+psycopg")
    url = url.replace("+psycopg2", "").replace("+psycopg", "").replace("+asyncpg", "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


class MidLookup:
    """在 (ts, mid) 上做「第一个 ts' >= ts + Δ」的查询。"""

    def __init__(self, ts, mid):
        import numpy as np
        self.ts = ts
        self.mid = mid
        self.np = np

    def at(self, t_ms: int, delta_ms: int):
        np = self.np
        i = int(np.searchsorted(self.ts, t_ms + delta_ms, side="left"))
        if i >= len(self.ts):
            return None
        return float(self.mid[i])


def run_symbol(sym: str, hours: float, quote_every_ms: int, horizons):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours * 3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, bid_px::float AS b, ask_px::float AS a,"
        "       bid_qty::float AS bq, ask_qty::float AS aq"
        f"  FROM asterdex_book_ticker WHERE symbol = %s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms",
        (sym,),
    )
    bt = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float AS p, qty::float AS q, is_buyer_maker AS ibm"
        f"  FROM asterdex_trades WHERE symbol = %s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms",
        (sym,),
    )
    tr = cur.fetchall()
    cn.close()

    if len(bt) < 1000 or len(tr) < 200:
        return None

    q_ts = np.array([int(r["event_ts_ms"]) for r in bt], dtype=np.int64)
    q_b = np.array([float(r["b"]) for r in bt])
    q_a = np.array([float(r["a"]) for r in bt])
    q_bq = np.array([float(r["bq"] or 0.0) for r in bt])
    q_aq = np.array([float(r["aq"] or 0.0) for r in bt])
    q_mid = (q_b + q_a) / 2.0
    mid = MidLookup(q_ts, q_mid)

    t_ts = np.array([int(r["event_ts_ms"]) for r in tr], dtype=np.int64)
    t_px = np.array([float(r["p"]) for r in tr])
    t_qty = np.array([float(r["q"]) for r in tr])
    # is_buyer_maker=True ⇒ 主动方是**卖方**（taker sell）
    t_is_sell = np.array([bool(r["ibm"]) for r in tr])

    # 报价网格（不必每个 tick 都报价；15s tick 的现实节奏更重要）
    step = max(1, int(quote_every_ms / max(1, int(np.median(np.diff(q_ts)) or 100))))
    qidx = np.arange(0, len(q_ts) - 1, step, dtype=np.int64)

    out = {}
    for qpos in (1.0, 0.5, 0.0):
        # qpos=1.0 排在队尾（LA=全部挂量）；0.0 队首（LA=0）
        recs = []
        for i in qidx:
            t0 = int(q_ts[i])
            # 只在买侧做（卖侧对称，留待扩展）；报价 = 当前最优买价
            px = float(q_b[i])
            la = float(q_bq[i]) * qpos
            j = int(np.searchsorted(t_ts, t0, side="left"))
            cum = 0.0
            fill_j = -1
            lim = int(np.searchsorted(t_ts, t0 + 300_000, side="left"))  # 5 分钟未见成交则撤
            while j < lim:
                if t_is_sell[j] and abs(t_px[j] - px) / px * 1e4 < 1.0:
                    cum += float(t_qty[j])
                    if cum >= la:
                        fill_j = j
                        break
                j += 1
            if fill_j < 0:
                continue
            tf = int(t_ts[fill_j])
            m = {"qpos": qpos, "t0": t0, "tf": tf, "px": px, "wait_ms": tf - t0,
                 "la_usd": la * px, "cum_usd": cum * px}
            for h in horizons:
                mm = mid.at(tf, h)
                m[f"mk{h}"] = ((mm / px - 1.0) * 1e4) if mm else None
            recs.append(m)
        out[qpos] = recs
    return {"symbol": sym, "n_quotes": int(len(qidx)), "by_qpos": out}


def summarize(recs, h):
    import numpy as np
    v = np.array([r[f"mk{h}"] for r in recs if r.get(f"mk{h}") is not None], dtype=float)
    if len(v) == 0:
        return None
    return {
        "n": int(len(v)),
        "mean_bp": float(v.mean()),
        "median_bp": float(np.median(v)),
        "stdev_bp": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
        "t": float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 1 and v.std() > 0 else 0.0,
        "pos_rate": float((v > 0).mean()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--symbols", default="ASTERUSDT,XRPUSDT,SOLUSDT,DOGEUSDT,UNIUSDT")
    ap.add_argument("--quote-every-ms", type=int, default=15000,
                    help="报价节奏（我们线上是 15s tick）")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print("H19 队列消耗制成交模型")
    print(f"窗口={args.hours}h  币={syms}  报价节奏={args.quote_every_ms}ms")
    print(f"markout 时点={MARKOUT_MS}ms（**从限价算**，与论文 Table 1 同口径）\n")

    results = []
    for s in syms:
        r = run_symbol(s, args.hours, args.quote_every_ms, MARKOUT_MS)
        if r is None:
            print(f"  {s:<12} 数据不足 → 跳过")
            continue
        results.append(r)
        print(f"  {s:<12} 报价 {r['n_quotes']:>6} 次")
        for qpos in (1.0, 0.5, 0.0):
            recs = r["by_qpos"][qpos]
            label = {1.0: "队尾(LA=全部)", 0.5: "队中(LA=一半)", 0.0: "队首(LA=0)"}[qpos]
            if not recs:
                print(f"      {label:<16} 0 笔成交")
                continue
            fill_rate = len(recs) / max(1, r["n_quotes"])
            waits = [x["wait_ms"] for x in recs]
            waits.sort()
            cells = []
            for h in MARKOUT_MS:
                s2 = summarize(recs, h)
                cells.append("—" if not s2 else f"{s2['mean_bp']:+.3f}")
            print(f"      {label:<16} 成交 {len(recs):>5} ({fill_rate*100:5.2f}%)  "
                  f"等待中位 {waits[len(waits)//2]/1000:5.1f}s  "
                  f"mk@1s/5s/30s/300s = {' / '.join(cells)}")

    # 汇总：队列位置的影响
    print("\n[汇总] 队列位置对 markout 的影响（跨币平均，**同格差 = 论文的杠杆**）")
    for h in MARKOUT_MS:
        line = f"  mk@{h/1000:>5.0f}s  "
        for qpos in (1.0, 0.5, 0.0):
            vals = []
            for r in results:
                s2 = summarize(r["by_qpos"][qpos], h)
                if s2:
                    vals.append(s2["mean_bp"])
            import numpy as np
            line += f"  LA系数{qpos}: {np.mean(vals):+7.3f}bp (n={len(vals)}币)" if vals else "  —"
        # 队首 − 队尾
        a, b = [], []
        for r in results:
            sa = summarize(r["by_qpos"][0.0], h)
            sb = summarize(r["by_qpos"][1.0], h)
            if sa and sb:
                a.append(sa["mean_bp"])
                b.append(sb["mean_bp"])
        if a:
            import numpy as np
            diff = np.array(a) - np.array(b)
            print(line + f"   ⇒ 队首−队尾 = {diff.mean():+.3f}bp")
        else:
            print(line)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outp = OUT_DIR / "h19_queue_fill_model.json"
    ser = []
    for r in results:
        ser.append({
            "symbol": r["symbol"], "n_quotes": r["n_quotes"],
            "by_qpos": {str(k): [{kk: vv for kk, vv in x.items() if kk != "qpos"}
                                 for x in v] for k, v in r["by_qpos"].items()},
        })
    outp.write_text(json.dumps({
        "hours": args.hours, "quote_every_ms": args.quote_every_ms,
        "markout_ms": MARKOUT_MS, "results": ser,
    }, ensure_ascii=False), encoding="utf-8")
    print(f"\n已写入 {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
