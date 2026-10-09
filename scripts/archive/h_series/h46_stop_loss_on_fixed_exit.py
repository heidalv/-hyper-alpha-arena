"""H46：在「不向下追价」上叠加浮亏止损 —— 把 p5 从 −21bp 硬截断。

## 动机（H45 留下的最优基础）

H45 三种出场纪律：
    E0 向下追价   均值 −0.3887   中位 +0.7247   p5  **−8.27**   胜率 53.8%  强平 0.0%
    E1 不向下追   均值 −0.9911   中位 **+0.9190** p5 **−21.09**  胜率 **66.2%** 强平 8.0%

**E1 的中位与胜率都显著更好，只是左尾更厚。**
⇒ 若能把左尾硬截断，均值就有机会转正。

## 做法

在 E1（出场目标固定在 `入场时最优卖价 × (1+w)`，不向下移动）之上叠加：

    **止损**：持仓期间若中价相对入场价跌超 `stop_bp`，**立即按中价 taker 平仓**

这**正是线上引擎的 `stop_loss_bp` 语义**（线上设 60bp，配合 `stop_loss_vol_min=1.0`
的波动闸，实测从未武装）。

## ⚠️ 反向检查（必须做，否则会犯 F230 的错）

本项目已有一条实测结论（`core.py` F230）：
**常数止损在正常日会「被震荡反复打止损、白付 taker 腿」而多亏**，
只在**高波动 regime** 才划算。

⇒ 所以本脚本**同时报两件事**：
  1. 均值是否改善（止损的收益）
  2. **止损触发率**（止损被打了多少次）—— 若触发率很高，说明在付大量 taker 费

**并且必须做对照**：`stop_bp=0`（不止损）作为基准。

## 判据（事先定死）

  · 均值转正 且 强平+止损 合计 < 40% ⇒ **止损就是解**
  · 均值改善但触发率高 ⇒ 方向对，需加波动闸（只在 σ 高时启用）
  · 均值无改善 ⇒ 止损只是把亏损换了个位置

用法：
    .venv\\Scripts\\python.exe scripts\\h46_stop_loss_on_fixed_exit.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h46_stop_loss.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, w_bp, quote_every_s, max_hold_s, requote_s,
               maker_fee_bp, taker_fee_bp, stops):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"
    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    bt = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tk = cur.fetchall()
    cn.close()
    if len(dep) < 2000 or len(bt) < 1000 or len(tk) < 2000:
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    tts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in bt])
    tsell = np.array([bool(x["ibm"]) for x in bt])
    kts = np.array([int(x["event_ts_ms"]) for x in tk], dtype=np.int64)
    kask = np.array([float(x["a"] or 0) for x in tk])
    kbid = np.array([float(x["b"] or 0) for x in tk])
    kmid = (kbid + kask) / 2.0

    def ask_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        return float(kask[i]) if i < len(kts) else 0.0

    def mid_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        return float(kmid[i]) if i < len(kts) else 0.0

    # 中价序列的时间轴（止损检查用）
    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))

    res = {stop: {"pnl": [], "holds": [], "forced": 0, "stopped": 0} for stop in stops}
    for i in qidx:
        bid = float(dbp[i])
        if bid <= 0:
            continue
        px_buy = bid * (1.0 - w_bp / 1e4)
        t0 = int(dts[i])
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
        je = -1
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] and tpx[jj] <= px_buy * (1.0 + 1.0 / 1e4):
                je = jj
                break
        if je < 0:
            continue
        t_entry = int(tts[je])
        entry_px = float(tpx[je])
        a_entry = ask_at(t_entry)
        if a_entry <= 0:
            continue
        deadline = t_entry + max_ms
        target_fixed = a_entry * (1.0 + w_bp / 1e4)

        for stop in stops:
            exit_px = None
            t_exit = None
            stopped = False
            t_cur = t_entry
            stop_level = entry_px * (1.0 - stop / 1e4) if stop > 0 else 0.0
            while t_cur < deadline:
                t_next = min(t_cur + rq, deadline)
                # ① 先查止损（先用中价越线判定，粗粒度但方向正确）
                if stop > 0:
                    i0 = int(np.searchsorted(kts, t_cur, "left"))
                    i1 = int(np.searchsorted(kts, t_next, "left"))
                    if i1 > i0:
                        seg = kmid[i0:i1]
                        hit = np.flatnonzero(seg <= stop_level)
                        if len(hit):
                            # 止损腿：taker 卖出，价格取该时刻中价
                            mm = float(seg[hit[0]])
                            exit_px = mm * (1.0 - taker_fee_bp / 1e4)
                            t_exit = int(kts[i0 + int(hit[0])])
                            stopped = True
                            break
                # ② 再查被动出场（固定在 target_fixed，不向下追）
                j2 = int(np.searchsorted(tts, t_cur, "left"))
                j3 = int(np.searchsorted(tts, t_next, "left"))
                for jj in range(j2, min(j3, len(tts))):
                    if (not tsell[jj]) and tpx[jj] >= target_fixed * (1.0 - 1.0 / 1e4):
                        exit_px = float(tpx[jj])
                        t_exit = int(tts[jj])
                        break
                if exit_px is not None:
                    break
                t_cur = t_next
            if exit_px is None:
                mm = mid_at(deadline)
                if mm <= 0:
                    continue
                net = (mm * (1.0 - taker_fee_bp / 1e4) - entry_px) / entry_px * 1e4 - maker_fee_bp
                res[stop]["pnl"].append(net)
                res[stop]["holds"].append(deadline - t_entry)
                res[stop]["forced"] += 1
            elif stopped:
                net = (exit_px - entry_px) / entry_px * 1e4 - maker_fee_bp
                res[stop]["pnl"].append(net)
                res[stop]["holds"].append(t_exit - t_entry)
                res[stop]["stopped"] += 1
            else:
                net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
                res[stop]["pnl"].append(net)
                res[stop]["holds"].append(t_exit - t_entry)

    out = {"symbol": s, "by_stop": {}}
    for stop in stops:
        d = res[stop]
        if not d["pnl"]:
            out["by_stop"][str(stop)] = None
            continue
        p = np.array(d["pnl"])
        out["by_stop"][str(stop)] = {
            "n": len(p), "n_forced": d["forced"], "n_stopped": d["stopped"],
            "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
            "p5": float(np.percentile(p, 5)), "p25": float(np.percentile(p, 25)),
            "p95": float(np.percentile(p, 95)),
            "win_rate": float((p > 0).mean()),
            "hold_ms_median": float(np.median(d["holds"])),
            "forced_rate": d["forced"] / max(1, len(p)),
            "stop_rate": d["stopped"] / max(1, len(p)),
        }
    return out


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--width-bp", type=float, default=0.5)
    ap.add_argument("--stops", default="0,2,3,5,8,12",
                    help="止损线（bp）；0=不止损（基准）")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    stops = [float(x) for x in args.stops.split(",")]

    print("H46 在「不向下追价」上叠加浮亏止损")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽={args.width_bp}bp  "
          f"止损线={stops}  最长持有={args.max_hold_s:.0f}s\n")

    per = []
    for s in syms:
        r = run_symbol(s, args.hours, args.width_bp, args.quote_every_s, args.max_hold_s,
                       args.requote_s, args.maker_fee_bp, args.taker_fee_bp, stops)
        if r:
            per.append(r)
    if not per:
        print("无数据")
        return 1

    print("[1] 各止损线（跨币按往返数加权）")
    print("    %7s %8s %10s %9s %9s %9s %8s %8s %8s"
          % ("止损bp", "往返数", "均值", "中位", "p5", "胜率", "止损率", "强平率", "$30腿"))
    summary = {}
    # ⚠️ 键必须**统一**：`stops` 是 float 列表（0.0/2.0/...），
    #    而 `str(0.0)` = "0.0"、"str(2.0)" = "2.0"。
    #    早先用 `str(int(stop)) if stop == int(stop) else str(stop)` 生成 "0"/"2"，
    #    与写入时的 `str(stop)`（"0.0"/"2.0"）**不匹配** ⇒ 全部查空。
    #    （同类错误本会话已第 3 次：字典键的字符串形态必须只由一处生成。）
    key_of = lambda z: str(float(z))
    for stop in stops:
        rows = [r["by_stop"][key_of(stop)] for r in per
                if r["by_stop"].get(key_of(stop))]
        if not rows:
            print("    %7.1f %8s  （无数据，键=%s）" % (stop, "-", key_of(stop)))
            continue
        N = sum(r["n"] for r in rows)
        mean = sum(r["net_bp_mean"] * r["n"] for r in rows) / N
        med = float(np.median([r["net_bp_median"] for r in rows]))
        p5 = float(np.median([r["p5"] for r in rows]))
        wr = sum(r["win_rate"] * r["n"] for r in rows) / N
        sr = sum(r["stop_rate"] * r["n"] for r in rows) / N
        fr = sum(r["forced_rate"] * r["n"] for r in rows) / N
        summary[stop] = {"n": N, "net_bp_mean": mean, "net_bp_median_sym": med,
                         "p5_median": p5, "win_rate": wr, "stop_rate": sr,
                         "forced_rate": fr, "usd_per_rt_30": mean / 1e4 * 30}
        print("    %7.1f %8d %+10.4f %+9.4f %+9.3f %8.1f%% %7.1f%% %7.1f%% %+9.6f"
              % (stop, N, mean, med, p5, 100 * wr, 100 * sr, 100 * fr, mean / 1e4 * 30))

    base = summary.get(0.0)
    if base:
        print("\n[2] 相对基准（stop=0，即 H45 的 E1）")
        print("    %7s %14s %14s %12s" % ("止损bp", "均值改善", "p5改善", "止损率"))
        for stop in stops:
            if stop == 0 or stop not in summary:
                continue
            v = summary[stop]
            print("    %7.1f %+14.4f %+14.3f %11.1f%%"
                  % (stop, v["net_bp_mean"] - base["net_bp_mean"],
                     v["p5_median"] - base["p5_median"], 100 * v["stop_rate"]))

    print("\n[3] 判据（事先定死：均值转正 且 止损+强平 < 40%）")
    cand = [(k, v) for k, v in summary.items() if k > 0]
    if cand:
        best = max(cand, key=lambda kv: kv[1]["net_bp_mean"])
        v = best[1]
        tail = v["stop_rate"] + v["forced_rate"]
        if v["net_bp_mean"] > 0 and tail < 0.40:
            print("    ⇒ ✓ **止损就是解**：stop=%.0fbp 均值 %+.4fbp，尾部成本 %.1f%%"
                  % (best[0], v["net_bp_mean"], 100 * tail))
        elif v["net_bp_mean"] > (base["net_bp_mean"] if base else -9e9):
            print("    ⇒ ~ 有改善（stop=%.0f，均值 %+.4fbp）但未转正或尾部成本 %.1f%% 过高"
                  % (best[0], v["net_bp_mean"], 100 * tail))
            if v["stop_rate"] > 0.25:
                print("       止损触发率 %.1f%% 偏高 ⇒ 正是 F230 记录的"
                      "「常数止损在正常日被震荡反复打止损」现象" % (100 * v["stop_rate"]))
                print("       ⇒ 应加波动闸（只在 σ_norm 高时启用止损），而不是常数止损")
        else:
            print("    ⇒ ✗ 止损无改善（最优 stop=%.0f 均值 %+.4fbp）" % (best[0], v["net_bp_mean"]))

    print("\n[4] 分币明细（stop=0 vs 最优）")
    bstop = max(cand, key=lambda kv: kv[1]["net_bp_mean"])[0] if cand else None
    for m in ([0.0, bstop] if bstop else [0.0]):
        print("    --- stop=%.0fbp ---" % m)
        for r in per:
            v = r["by_stop"].get(key_of(m))
            if not v:
                continue
            print("      %-8s n=%5d  均值 %+8.4fbp  中位 %+8.4fbp  p5 %+8.3f  "
                  "胜率 %5.1f%%  止损 %5.1f%%  强平 %5.1f%%"
                  % (r["symbol"], v["n"], v["net_bp_mean"], v["net_bp_median"],
                     v["p5"], 100 * v["win_rate"], 100 * v["stop_rate"],
                     100 * v["forced_rate"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "width_bp": args.width_bp,
        "stops": stops, "max_hold_s": args.max_hold_s,
        "per_symbol": per, "summary": {str(k): v for k, v in summary.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
