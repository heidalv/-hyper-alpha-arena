"""H28：引擎的成交价，在真实逐笔成交里真的存在吗？

## 为什么必须验（H27 暴露的矛盾）

H27 用**真实盘口**重算了每一腿的"半价差"（成交时中价相对我们成交价的距离）：

    entry 买单  -1.044bp      entry 卖单  -2.868bp
    exit  买单  -2.175bp      exit  卖单  -2.483bp     ← **全是负的**

**做市挂单在成交瞬间不可能比中价差**：买单只能成交在中价或更低价，卖单只能在中价或更高价。
"半价差为负"在物理上只能意味着**我们记的时刻/价格与真实成交不同步**。

而 H24 的反事实（严格按逐笔成交 + 队列消耗制）给出 **净 +1.27bp**。两者冲突。

## 机制假设（本脚本要证伪或证实）

引擎的成交判定是（`core.fill_side`，`PENETRATION_BP=0`）：

    hit_buy = seg_taker_sell > 0 and seg_low < quote_bid

`seg_low` 是 **15 秒桶**内的最低成交价。于是：

  · 桶内只要有**任何一笔**成交价低于我们的挂单价，就判我们成交；
  · 成交价记作 `quote_bid`（我们的挂单价），而不是那笔真实成交的价；
  · **桶内其余成交可能都在更好的价位**（桶跨 15 秒，价格会来回）。

⇒ 这会让成交样本**偏向桶内极值**，即**机械上最有毒的成交子集**。
这正是 Databento 警告的"L2-only replay 高估成交率、低估逆向选择"。

## 判定方法（不猜）

对每一笔引擎记录的成交，去 `asterdex_trades` 里查**真实逐笔**：

  ① 成交时刻 ±`--win-ms` 内，是否存在一笔真实成交满足
       · 价格与引擎记录的成交价相差 ≤ `--px-tol-bp`
       · 方向与我们的腿一致（买单 ⇒ 该笔是**主动卖**，即 `is_buyer_maker = True`）
  ② 统计"找得到对得上"与"找不到"的比例。
  ③ 对找得到的，比较**引擎成交价** vs **那笔真实成交价**的偏差。

判据（事先定死）：
  · 若"对得上"比例 **≥ 80%** ⇒ 引擎的成交价基本可信，H27 的负半价差是**真实逆向选择**
    （即我们报价后中价已移动），需另找解释。
  · 若 **< 50%** ⇒ **引擎的成交价在真实成交里不存在** ⇒ fill 模型产出的是
    虚构成交，账本所有策略结论（含 H26 的往返分解）都必须重做。
  · 介于两者之间 ⇒ 部分虚构，需按"对得上/对不上"分组重算。

用法：
    .venv\\Scripts\\python.exe scripts\\h28_verify_fill_prices.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
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


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--account-id", type=int, default=101)
    ap.add_argument("--win-ms", type=int, default=20000,
                    help="在引擎记录的成交时刻前后多大窗口内找真实成交")
    ap.add_argument("--px-tol-bp", type=float, default=2.0,
                    help="价格吻合容差（bp）")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    # ── ① 引擎记录的成交（账户账本，含 side/px/qty）────────────
    biz = psycopg2.connect(_dsn("alpha_arena"))
    biz.autocommit = True
    cur = biz.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT created_at, metadata_json
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, args.hours * 3600.0),
    )
    engine_fills = []
    for r in cur.fetchall():
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        side = str(md.get("side") or "").lower()
        if not side or not md.get("px"):
            continue
        t = r["created_at"]
        t_ep = (t.replace(tzinfo=datetime.now().astimezone().tzinfo).timestamp()
                if t.tzinfo is None else t.timestamp())
        engine_fills.append({
            "ts_ep": t_ep, "symbol": md.get("symbol") or "-",
            "is_buy": side.startswith("b"), "px": float(md["px"]),
            "qty": float(md.get("qty") or 0.0),
        })

    print(f"H28 引擎成交价 vs 真实逐笔成交  账户={args.account_id}  窗口={args.hours}h")
    print(f"        引擎记录 {len(engine_fills)} 笔")
    print(f"        匹配窗口 ±{args.win_ms}ms   价格容差 ±{args.px_tol_bp}bp\n")
    if not engine_fills:
        return 1

    # ── ② 逐币拉真实逐笔 ────────────────────────────────────────
    mkt = psycopg2.connect(_dsn("alpha_market"))
    mkt.autocommit = True
    cur2 = mkt.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    by_sym = defaultdict(list)
    for x in engine_fills:
        by_sym[x["symbol"]].append(x)

    res = {"match": 0, "nomatch": 0, "n": 0, "density": {}}
    px_dev = []
    details = []
    for s in sorted(by_sym):
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur2.execute(
            "SELECT event_ts_ms, price::float AS p, qty::float AS q, is_buyer_maker AS ibm"
            "  FROM asterdex_trades WHERE symbol = %s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, int((args.hours + 1) * 3600_000)),
        )
        tr = cur2.fetchall()
        if not tr:
            print(f"  {s:<10} 无逐笔数据 → 该币的腿无法验证")
            continue
        tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
        tpx = np.array([float(x["p"]) for x in tr])
        tq = np.array([float(x["q"]) for x in tr])
        tsell = np.array([bool(x["ibm"]) for x in tr])   # True = 主动卖
        span_min = max(1e-9, (int(tts[-1]) - int(tts[0])) / 60000.0)
        per_min = len(tts) / span_min

        n_ok = n_pxdev = n_notrade = 0
        for x in by_sym[s]:
            res["n"] += 1
            t0 = int(x["ts_ep"] * 1000)
            lo = int(np.searchsorted(tts, t0 - args.win_ms, "left"))
            hi = int(np.searchsorted(tts, t0 + args.win_ms, "left"))
            lo = max(0, min(lo, len(tts) - 1))
            hi = max(lo, min(hi, len(tts)))
            best = None
            n_opp = 0
            # 我们的买单 ⇒ 对手方主动卖（tsell=True）；卖单 ⇒ 主动买
            want_sell = x["is_buy"]
            for j in range(lo, hi):
                if tsell[j] != want_sell:
                    continue
                n_opp += 1
                d = abs(tpx[j] - x["px"]) / x["px"] * 1e4
                if best is None or d < best[0]:
                    best = (d, float(tpx[j]), float(tq[j]), int(tts[j]))
            if best is not None and best[0] <= args.px_tol_bp:
                n_ok += 1
                px_dev.append((x["px"] - best[1]) / x["px"] * 1e4)
                details.append({"symbol": s, "ok": True, "dev_bp": best[0],
                                "engine_px": x["px"], "real_px": best[1],
                                "engine_qty": x["qty"], "real_qty": best[2],
                                "dt_ms": best[3] - t0, "is_buy": x["is_buy"]})
            elif n_opp == 0:
                # 窗口内**根本没有对手方向的成交** ⇒ 数据不足，无法判定
                n_notrade += 1
                details.append({"symbol": s, "ok": None, "reason": "no_trade_in_window",
                                "engine_px": x["px"], "is_buy": x["is_buy"]})
            else:
                # 有成交但价格差超容差 ⇒ **价格偏差**（这才是真问题）
                n_pxdev += 1
                details.append({"symbol": s, "ok": False,
                                "dev_bp": best[0] if best else None,
                                "engine_px": x["px"], "engine_qty": x["qty"],
                                "real_px": best[1] if best else None,
                                "is_buy": x["is_buy"]})
        res["match"] += n_ok
        res["nomatch"] += n_pxdev + n_notrade
        res["density"][s] = {"per_min": per_min, "n": n_ok + n_pxdev + n_notrade,
                             "match": n_ok, "pxdev": n_pxdev, "notrade": n_notrade}
        print("  %-10s 引擎 %3d 笔   对得上 %3d   价格偏差 %3d   无成交可比 %3d   (%.1f/分)"
              % (s, n_ok + n_pxdev + n_notrade, n_ok, n_pxdev, n_notrade, per_min))

    print(f"\n[1] 总体")
    print("    引擎记录 %d 笔   对得上 %d   对不上 %d   ⇒ 吻合率 %.1f%%"
          % (res["n"], res["match"], res["nomatch"],
             100.0 * res["match"] / max(1, res["n"])))

    # ⚠️⚠️ 关键修正：总体吻合率**没有意义**，因为逐笔密度跨币差 500 倍。
    #   实测 8h 逐笔密度（ASTER 32/分、SOL 25/分 … UNI 1.16/分、VIRTUAL 0.14/分）：
    #   稀疏币的 ±20s 窗口里**根本没有真实成交**可比 ⇒ 必然 0% 吻合，
    #   那是"数据稀疏"，不是"虚构成交"。把两者混在一起算总体会得出错误结论。
    #   正确做法：**只在逐笔足够密的币上判定**，并区分
    #     (a) 窗口内完全没有对手方向的成交  → 无法判定（数据不足）
    #     (b) 有成交但价格差 > 容差        → **价格偏差**（这才是真问题）
    dens = res.get("density", {})
    print(f"\n[1b] 按逐笔密度分层（密度 = 该币 8h 内成交笔数 / 分钟）")
    print("     %-10s %10s %8s %8s %8s %8s"
          % ("symbol", "trades/min", "引擎腿", "对得上", "价格偏差", "无成交可比"))
    dense_ok = dense_bad = dense_none = 0
    for s, d in sorted(dens.items(), key=lambda kv: -kv[1]["per_min"]):
        print("     %-10s %10.2f %8d %8d %8d %8d"
              % (s, d["per_min"], d["n"], d["match"], d["pxdev"], d["notrade"]))
        if d["per_min"] >= 5.0:                 # 5 笔/分以上才算"有可比数据"
            dense_ok += d["match"]
            dense_bad += d["pxdev"]
            dense_none += d["notrade"]
    tot_dense = dense_ok + dense_bad + dense_none
    if tot_dense:
        print(f"\n[1c] **只在密度 ≥5 笔/分的币上判定**（{tot_dense} 笔引擎腿）")
        print("     对得上 %d   价格偏差 %d   无成交可比 %d"
              % (dense_ok, dense_bad, dense_none))
        print("     ⇒ 价格吻合率 = %.1f%%（= 对得上 / (对得上 + 价格偏差)）"
              % (100.0 * dense_ok / max(1, dense_ok + dense_bad)))

    if px_dev:
        d = np.array(px_dev)
        print("\n[2] 吻合的腿里，引擎成交价 vs 真实成交价")
        print("    偏差 中位 %+.3fbp   均值 %+.3fbp   p90|偏差| %.3fbp"
              % (np.median(d), d.mean(), np.percentile(np.abs(d), 90)))

    print("\n[3] 判定")
    if tot_dense:
        pr = 100.0 * dense_ok / max(1, dense_ok + dense_bad)
        if pr >= 80:
            print("    ⇒ 在**有可比数据**的币上，价格吻合率 %.0f%% ≥ 80%% ⇒ **引擎成交价可信**。" % pr)
            print("      早先那个 49%% 的总体吻合率是**测试缺陷**：它把稀疏币的")
            print("      「窗口内没有成交可比」错算成了「虚构成交」。")
            print("      ⇒ H27 的负半价差因此是**真实逆向选择**，不是记账错误。")
        elif pr < 50:
            print("    ⇒ 在有可比数据的币上吻合率仍只有 %.0f%% ⇒ **成交价系统性不符**。" % pr)
            print("      账本的一切策略结论必须重做。")
        else:
            print("    ⇒ 吻合率 %.0f%% 介于 50~80%% ⇒ 部分不符，需按对得上/对不上分组重算。" % pr)
    rate = res["match"] / max(1, res["n"])
    print("    （仅供参考：全体吻合率 %.1f%% —— **不要用这个数下结论**，它被稀疏币污染）"
          % (100 * rate))

    # 价格偏差样本的典型形态
    bad = [x for x in details if x.get("ok") is False]
    if bad:
        devs = [x["dev_bp"] for x in bad if x.get("dev_bp") is not None]
        print(f"\n[4] **价格偏差**的 {len(bad)} 笔里，最近的真实成交距我们记录的成交价：")
        if devs:
            a = np.array(devs)
            print("    中位 %.3fbp   p25 %.3fbp   p75 %.3fbp   max %.3fbp"
                  % (np.median(a), np.percentile(a, 25), np.percentile(a, 75), a.max()))
            print("    ⇒ 偏差大 = 我们记录的成交价在该时刻**真实市场里没有对应成交**。")
        side_dev = defaultdict(list)
        for x in bad:
            if x.get("dev_bp") is not None:
                side_dev["buy" if x["is_buy"] else "sell"].append(x["dev_bp"])
        for k, v in side_dev.items():
            if v:
                print("    %-5s n=%3d 中位偏差 %+.3fbp" % (k, len(v), float(np.median(v))))
    # 无成交可比的样本单独列出（不该混进价格偏差）
    nt = [x for x in details if x.get("ok") is None]
    if nt:
        print(f"\n[5] 「窗口内没有对手方向成交」{len(nt)} 笔 —— **无法判定**，"
              f"不是价格偏差（集中在稀疏币）")
        bys = defaultdict(int)
        for x in nt:
            bys[x["symbol"]] += 1
        print("    " + "  ".join(f"{k}:{v}" for k, v in sorted(bys.items())))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h28_fill_price_verification.json"
    p.write_text(json.dumps({"hours": args.hours, "win_ms": args.win_ms,
                             "px_tol_bp": args.px_tol_bp,
                             "n": res["n"], "match": res["match"],
                             "nomatch": res["nomatch"],
                             "match_rate": rate,
                             "details": details[:500]},
                            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    biz.close()
    mkt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
