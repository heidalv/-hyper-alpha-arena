"""H38：报价位置扫描 —— 找「每笔净额」最大的挂单距离。

## 为什么这是让策略转正的最高价值一步

H34 修正后的分解（tick 级，24h，5 币）：

    捕获@成交 = **+0.200 bp/笔**      而名义半价差 = 0.750 bp
    ⇒ 捕获率只有 **26.7%**；其余 0.55bp 被逆向选择吃掉
    净/笔 = 捕获 + markout = −0.0202bp（P0）~ −0.0073bp（P2 反势挂）

**捕获率是最大的那块。** 而捕获率是**报价位置的函数**：
  · 挂得越远（越深入订单簿）⇒ 名义半价差越大，但只有更大的逆向移动才够得着
  · 挂得越近（贴 touch / 进价差内）⇒ 名义半价差越小，但成交概率高、捕获率可能更高

**H24 曾用桶级口径测过"挂宽更好"，但那个结论被 `abs(mk)` 污染了 ⇒ 必须用 tick 级重测。**

## 扫描的价位（相对**最优买价**的偏移，bp）

    w_off = 0.0   ← 挂在 touch 上（= 加入现有队列的队尾）
    w_off = 0.5 / 1.0 / 2.0 / 3.0 / 5.0 / 10.0   ← 挂在 touch **之外**（深入订单簿）
    w_off = −0.5 / −1.0                          ← **进价差内**（改善最优价）

## 成交模型（论文口径，同量纲）

    · 挂单价 = `bid_px × (1 − w_off/1e4)`（w_off>0 在外；<0 为改善最优价）
    · 前方挂量 LA：
        w_off > 0 → 该价位**没有人的队列**（我们在更深的档）⇒ LA = 我们自己的量，
                    即**没人排在我们前面** ⇒ 用 LA=0（但要价格真的跌到那里）
        w_off = 0 → LA = 最优档挂量（我们排在现有队列的**最后**，保守）
        w_off < 0 → LA = 0（我们创造了新的最优价，队列里只有我们）
    · 成交条件：自挂单起，**在该价位的累计对手方主动量 ≥ LA**
    · markout 从**限价**算，**成交后固定 τ 秒**（不是桶末）
    · 净额 = 成交时半价差 + markout（**有符号**，见 `_fix_abs_markout.py`）

## 判据（事先定死）

  · 找出「每笔净额」最大的 w_off；若其 > **+0.05bp/笔** ⇒ 该位置值得上线
  · 同时报**成交率**（挂远了成交率会掉）与**每决策净额**
  · 若所有 w_off 的每笔净额都 < 0 ⇒ 报价位置不是解，转向信号/出场

用法：
    .venv\\Scripts\\python.exe scripts\\h38_quote_offset_sweep.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h38_quote_offset_sweep.json"
# 报价位置偏移（bp，相对最优买价；正=更深，负=进价差内）
OFFSETS = [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, -0.5, -1.0]
TAU_MS = 5000        # markout 时点（H34 显示 1s/5s/30s 差别小，取中间）


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, targets, max_wait_s, quote_every_s, log):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp, (bids->0->>1)::float bq,"
        "       (asks->0->>0)::float ap, (asks->0->>1)::float aq"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    bt = cur.fetchall()
    cn.close()
    if len(dep) < 2000 or len(tr) < 1000 or len(bt) < 2000:
        log(f"  {s:<8} 数据不足 → 跳过")
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    dbq = np.array([float(x["bq"] or 0) for x in dep])
    tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in tr])
    tq = np.array([float(x["q"]) for x in tr])
    tsell = np.array([bool(x["ibm"]) for x in tr])
    bts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    bmid = (np.array([float(x["b"]) for x in bt])
            + np.array([float(x["a"]) for x in bt])) / 2.0

    def mid_at(t_ms, delta):
        i = int(np.searchsorted(bts, t_ms + delta, "left"))
        return float(bmid[i]) if i < len(bts) else None

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)

    # 每个 offset 一条记录
    acc = {w: [] for w in targets}
    n_dec = {w: 0 for w in targets}
    for i in qidx:
        bid = float(dbp[i])
        la_touch = float(dbq[i])
        if bid <= 0:
            continue
        t0 = int(dts[i])
        # 该报价的成交窗口内的逐笔
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, t0 + int(max_wait_s * 1000), "left"))
        # 窗口内的深度快照下标范围（用于"价格是否真的到过我们的价位"）
        t_end = int(tts[min(j1, len(tts) - 1)]) if j1 > 0 else t0
        di0 = int(np.searchsorted(dts, t0, "left"))
        di1 = int(np.searchsorted(dts, t_end, "right"))
        if di1 <= di0:
            continue
        win_dbp = dbp[di0:di1]
        win_ok = win_dbp > 0

        for w in targets:
            px = bid * (1.0 - w / 1e4)
            if px <= 0:
                continue
            # 前方挂量：touch 上排最后；进价差内/更深档 = 无人排在我们前面
            la = la_touch if abs(w) < 1e-12 else 0.0
            n_dec[w] += 1

            # ── 守卫 1：**价格必须真的触到过我们的价位** ──────────────────
            #
            # 这是上一版的致命 bug：只判了"买价没有跌破我们的价"，**没判有没有到过**。
            # 结果 w=+0.5 只多让出 0.5bp，却拿到 83% 成交率与 +0.59bp 捕获 ——
            # 而中价最多只在最优买价上方**半个价差**，捕获不可能超过 0.5bp。
            # 物理上：挂得比最优买价更深 ⇒ 价格必须**下来触到**我们才可能成交。
            if not (win_ok & (win_dbp <= px * (1.0 + 1.0 / 1e4))).any():
                continue

            cum = 0.0
            fill_j = -1
            for jj in range(j0, min(j1, len(tts))):
                tj = int(tts[jj])
                di = int(np.searchsorted(dts, tj, "right")) - 1
                if di < 0:
                    continue
                cur_bid = float(dbp[di])
                if cur_bid <= 0:
                    continue
                # ── 内部一致性条件（这是唯一可靠的口径）──────────────────
                #
                # 一笔挂在 `px` 的买单要成为 maker 成交，**成交瞬间 `px` 必须就是最优买价**：
                #   · `px < cur_bid` ⇒ 有更优的买价在我们前面 ⇒ 我们**不在** touch ⇒ 不该成交
                #   · `px > cur_bid` ⇒ 我们的价优于市场最优价（进价差内），
                #                     一旦有人打到我们就成交（队列里只有我们）
                # 早先只用"买价没跌破"的宽松守卫 ⇒ w=+0.5 拿到 83% 成交率与
                # +0.58bp 捕获，**物理上不可能**（中价最多在最优买价上方半个价差）。
                _tol = 0.1 / 1e4
                if px < cur_bid * (1.0 - _tol):
                    break                      # 有更优买价 ⇒ 我们的单不在 touch
                if tsell[jj] and abs(tpx[jj] - px) / px * 1e4 < 1.0:
                    cum += float(tq[jj])
                    if cum >= la:
                        fill_j = jj
                        break
            if fill_j < 0:
                continue
            tf = int(tts[fill_j])
            ii = int(np.searchsorted(bts, tf, "left"))
            if ii >= len(bts):
                continue
            mid_fill = float(bmid[ii])
            half = (mid_fill - px) / px * 1e4          # 成交时半价差（正=捕获到）
            mm = mid_at(tf, TAU_MS)
            if mm is None:
                continue
            mk = (mm / px - 1.0) * 1e4                 # 有符号：正=有利
            acc[w].append({"half": half, "mk": mk, "net": half + mk})

    out = {"symbol": s, "n_quotes": int(len(qidx)), "by_offset": {}}
    for w in targets:
        recs = acc[w]
        if not recs:
            out["by_offset"][str(w)] = None
            continue
        a = np.array([r["net"] for r in recs])
        h = np.array([r["half"] for r in recs])
        m = np.array([r["mk"] for r in recs])
        out["by_offset"][str(w)] = {
            "n_dec": n_dec[w], "n_fill": len(recs),
            "fill_rate": len(recs) / max(1, n_dec[w]),
            "half_bp": float(h.mean()), "mk_bp": float(m.mean()),
            "net_bp_per_fill": float(a.mean()),
            "net_bp_per_dec": float(a.sum() / max(1, n_dec[w])),
            "net_median_bp": float(np.median(a)),
        }
    return out


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--max-wait-s", type=float, default=120.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H38 报价位置扫描（tick 级，同量纲，markout 有符号）")
    print(f"窗口={args.hours}h  币={len(syms)}  报价节奏={args.quote_every_s:.0f}s  "
          f"markout τ={TAU_MS}ms  最长等待={args.max_wait_s:.0f}s")
    print("偏移 w：正=挂在最优买价之外（更深）；0=贴 touch；负=进价差内\n")

    res = []
    for s in syms:
        r = run_symbol(s, args.hours, OFFSETS, args.max_wait_s, args.quote_every_s, print)
        if r:
            res.append(r)
    if not res:
        print("无数据")
        return 1

    print("\n[1] 各偏移的每笔净额（跨币按成交数加权）")
    print("    %8s %10s %10s %10s %12s %13s %12s"
          % ("偏移bp", "决策数", "成交数", "成交率", "半价差@成交", "markout", "净/笔"))
    summary = {}
    for w in OFFSETS:
        tot_net = tot_fill = tot_dec = 0
        hs, mks = [], []
        for r in res:
            v = r["by_offset"].get(str(w))
            if not v:
                continue
            tot_dec += v["n_dec"]
            tot_fill += v["n_fill"]
            tot_net += v["net_bp_per_fill"] * v["n_fill"]
            hs.append(v["half_bp"] * v["n_fill"])
            mks.append(v["mk_bp"] * v["n_fill"])
        if not tot_fill:
            print("    %8.1f %10s" % (w, "无成交"))
            continue
        summary[w] = {
            "n_dec": tot_dec, "n_fill": tot_fill,
            "fill_rate": tot_fill / max(1, tot_dec),
            "half_bp": sum(hs) / tot_fill,
            "mk_bp": sum(mks) / tot_fill,
            "net_bp_per_fill": tot_net / tot_fill,
            "net_bp_per_dec": tot_net / max(1, tot_dec),
        }
        v = summary[w]
        print("    %+8.1f %10d %10d %9.2f%% %12.4f %13.4f %12.4f"
              % (w, v["n_dec"], v["n_fill"], 100 * v["fill_rate"],
                 v["half_bp"], v["mk_bp"], v["net_bp_per_fill"]))

    if not summary:
        return 1
    best = max(summary.items(), key=lambda kv: kv[1]["net_bp_per_fill"])
    print("\n[2] 最优报价位置")
    print("    w = %+.1f bp  ⇒ 每笔净额 %+.4f bp（成交率 %.1f%%）"
          % (best[0], best[1]["net_bp_per_fill"], 100 * best[1]["fill_rate"]))
    print("    对照现状（w=+1.5bp 是线上挂宽，本表未列；最近的档位是 w=+0.5/+2.0）")

    print("\n[3] 判据（事先定死：每笔净额 > +0.05bp 才值得上线）")
    if best[1]["net_bp_per_fill"] > 0.05:
        print("    ⇒ ✓ w=%+.1f 的每笔净额 %+.4fbp > +0.05bp ⇒ **值得上线**"
              % (best[0], best[1]["net_bp_per_fill"]))
    elif best[1]["net_bp_per_fill"] > 0:
        print("    ⇒ ~ w=%+.1f 为正（%+.4fbp）但 < +0.05bp ⇒ 不显著"
              % (best[0], best[1]["net_bp_per_fill"]))
    else:
        print("    ⇒ ✗ 所有报价位置的每笔净额都 ≤ 0 ⇒ **报价位置不是解**，")
        print("      应转向信号/出场/费率，而不是继续调挂宽。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": args.hours, "offsets_bp": OFFSETS,
                               "tau_ms": TAU_MS, "results": res,
                               "summary": {str(k): v for k, v in summary.items()}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
