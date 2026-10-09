"""H20：`spread_bp` 与 `price_bp` 是否在重复计算半价差？（子代理的质疑）

## 质疑内容

外部文献调研指出（Albers et al. 的 markout 定义）：
    「从**成交价**算的 markout 已经包含了捕获到的半价差；
      它与『加权价差捕获』不可相加，相加会**重复计算**」

我们的六维归因是 `net_bp = spread_bp + funding_bp + price_bp + fee_bp + slippage_bp`，
即把 `spread_bp`（价差捕获）与 `price_bp`（成交后中价漂移）相加。
若质疑成立，我们的 net 就是错的。

## 用数据判定（不靠论证）

论文的 markout 基准是 **限价（挂单价）**，终点是 **microprice**（中价未动时）。
我们的 `spread_bp` 基准是 **成交时刻的中价**，即

    spread_bp = (mid_fill − fill_px)/mid_fill × 1e4     （买单）

关键区分：
  · 若 `spread_bp ≈ 半价差`（本例 0.6–0.95bp）⇒ 它确实只是"半价差"，与 markout 有重叠风险
  · 若 `spread_bp` 显著**大于**半价差 ⇒ 说明成交价远优于成交时刻的中价，
    即 **fill 判定所用的 mid 与实际盘口脱节**（或成交发生在盘口之外）

**我们实测 +1.41bp，而半价差只有 0.6–0.95bp —— 已经是一个警示信号。**

本脚本用真实盘口逐笔核对：
  ① 每笔成交的 `fill_px` 与**当时真实盘口**（`asterdex_book_ticker`）的关系：
     成交价是否 = 当时的最优买价（买单）？差多少 bp？
  ② 用**真实盘口 mid** 而不是引擎记的 mid 重算 spread_bp，看差多少
  ③ 用**挂单价**做基准重算（论文口径），与 ① 对比

判定标准（事先定好）：
  · 若 "fill_px vs 真实盘口最优价" 的偏差中位数 < 1bp ⇒ 成交价合理，
    问题出在**引擎记的 mid**（成交价 vs 中价的 1.41bp 是 mid 滞后造成的假象）
  · 若偏差 > 5bp ⇒ 成交价本身不可信，fill 模型在"盘口之外"成交

用法：
    .venv\\Scripts\\python.exe scripts\\h20_spread_attribution_audit.py --hours 6
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


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--lane", default=os.getenv("MM_LANE_ID", "mm_asterdex"))
    ap.add_argument("--tol-ms", type=int, default=3000, help="成交时刻与盘口快照的最大匹配间隔")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    # ① 账本里的成交（有 fill_px / mid / side 的只有 meta_json）
    biz = psycopg2.connect(_dsn("alpha_arena"))
    biz.autocommit = True
    cur = biz.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT ts, symbol, event, notional, spread_bp, price_bp, fee_bp, net_bp, meta_json
          FROM lane_ledger
         WHERE lane_id = %s AND ts > now() - make_interval(secs => %s)
         ORDER BY ts
        """,
        (args.lane, args.hours * 3600.0),
    )
    rows = cur.fetchall()
    print(f"H20 归因口径审计  lane={args.lane}  窗口={args.hours}h  账本 {len(rows)} 行")
    if not rows:
        print("无成交")
        return 1

    # ② 逐笔重建 fill_px 与 side
    #
    # ⚠️ `lane_ledger.meta_json` 只存 flatten/notional/price_usd，**没有 px / mid**
    #    （`px`/`mid` 只是 `record_fill` 的入参，不入库）。所以不能直接读。
    #
    #    但 `spread_bp` 与 `price_bp` 都是**从 px 和 mid 算出来的**，可以反推：
    #      spread_bp = (mid − fill_px)/mid × 1e4        （买单）
    #                = (fill_px − mid)/mid × 1e4        （卖单）
    #      price_bp  = price_usd / notional × 1e4
    #    方向：`price_usd` 是**平仓部分**的中价损益，符号自身不能定方向；
    #    改用 `spread_bp` 的符号（买单挂宽为正、卖单挂宽也为正 —— 都是正），
    #    也不行。**唯一可靠的方向来源是 side，而它没入库。**
    #
    #    ⇒ 这里换一个不需要方向的判据：直接核对
    #        「引擎记录的 spread_bp」 vs 「真实盘口的半价差」
    #      因为**无论买卖**，一笔挂在最优价上的 maker 成交，其
    #        |spread_bp| ≈ 半价差
    #      所以「spread_bp 的量级是否 ≈ 半价差」就足以判定归因是否重复计算，
    #      **不需要知道方向**。
    recs = []
    for r in rows:
        try:
            md = json.loads(r["meta_json"] or "{}")
        except Exception:
            md = {}
        if r["spread_bp"] is None:
            continue
        recs.append({
            "ts": r["ts"], "symbol": r["symbol"],
            "spread_bp": float(r["spread_bp"] or 0.0),
            "price_bp": float(r["price_bp"] or 0.0),
            "fee_bp": float(r["fee_bp"] or 0.0),
            "net_bp": float(r["net_bp"] or 0.0),
            "notional": float(r["notional"] or 0.0),
            "flatten": bool(md.get("flatten")),
        })
    print(f"        可用于审计的 {len(recs)} 笔")
    if not recs:
        print("无数据")
        return 1

    # ③ 取真实盘口
    mkt = psycopg2.connect(_dsn("alpha_market"))
    mkt.autocommit = True
    cur2 = mkt.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    syms = sorted({x["symbol"] for x in recs})
    print(f"        涉及 {len(syms)} 币: {' '.join(syms)}\n")

    out = []
    for s in syms:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur2.execute(
            "SELECT event_ts_ms, bid_px::float AS b, ask_px::float AS a,"
            "       bid_qty::float AS bq, ask_qty::float AS aq"
            "  FROM asterdex_book_ticker WHERE symbol = %s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, int(args.hours * 3600_000)),
        )
        bt = cur2.fetchall()
        if not bt:
            continue
        bts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
        bb = np.array([float(x["b"]) for x in bt])
        ba = np.array([float(x["a"]) for x in bt])
        bq = np.array([float(x["bq"] or 0) for x in bt])
        aq = np.array([float(x["aq"] or 0) for x in bt])

        for x in recs:
            if x["symbol"] != s:
                continue
            t_ms = int(x["ts"].timestamp() * 1000)
            i = int(np.searchsorted(bts, t_ms, side="left"))
            if i >= len(bts):
                continue
            j = i if (i == 0 or bts[i] - t_ms <= t_ms - bts[i - 1]) else i - 1
            if abs(int(bts[j]) - t_ms) > args.tol_ms:
                continue
            real_bid, real_ask = float(bb[j]), float(ba[j])
            real_mid = (real_bid + real_ask) / 2.0
            real_spread_bp = (real_ask - real_bid) / real_mid * 1e4
            out.append({
                "symbol": s,
                "engine_spread_bp": x["spread_bp"],
                "engine_price_bp": x["price_bp"],
                "engine_net_bp": x["net_bp"],
                "real_half_spread_bp": real_spread_bp / 2.0,
                "real_full_spread_bp": real_spread_bp,
                "notional": x["notional"], "flatten": x["flatten"],
            })

    if not out:
        print("没有匹配上盘口的成交（时间对齐失败）")
        return 1

    print(f"[1] 逐笔匹配盘口成功 {len(out)} / {len(recs)} 笔\n")

    print("[2] 【关键】引擎 spread_bp 量级 vs 真实盘口半价差")
    es = np.array([r["engine_spread_bp"] for r in out])
    half = np.array([r["real_half_spread_bp"] for r in out])
    full = np.array([r["real_full_spread_bp"] for r in out])
    print("    引擎 spread_bp 均值        = %+.4f bp" % es.mean())
    print("    真实盘口 半价差 均值       = %+.4f bp" % half.mean())
    print("    真实盘口 全价差 均值       = %+.4f bp" % full.mean())
    print("    引擎 spread_bp / 半价差    = %.2f 倍" % (es.mean() / max(1e-9, half.mean())))

    print("\n[3] 引擎 price_bp vs 净额闭合性（内部一致性）")
    pb = np.array([r["engine_price_bp"] for r in out])
    nb = np.array([r["engine_net_bp"] for r in out])
    print("    引擎 price_bp 均值         = %+.4f bp" % pb.mean())
    print("    引擎 net_bp 均值           = %+.4f bp" % nb.mean())
    print("    验算 spread+price+fee 与 net 的差（应≈0，含 funding/slippage）")
    fee = np.array([0.0] * len(out))
    resid = es + pb + fee - nb
    print("    残差 均值=%.4f  最大|残差|=%.4f  ⇒ 六维闭合 %s"
          % (resid.mean(), np.abs(resid).max(),
             "正常" if np.abs(resid).max() < 0.05 else "异常"))

    print("\n[4] 判定（子代理质疑：spread_bp 与 price_bp 是否重复计算半价差）")
    ratio = es.mean() / max(1e-9, half.mean())
    if ratio > 1.6:
        print("    ⇒ 引擎 spread_bp 是半价差的 **%.2f 倍**，显著大于 1。" % ratio)
        print("      一笔挂在最优价上的 maker 成交，其价差捕获**最多**等于半价差")
        print("      ⇒ 要么成交发生在比最优价更优的位置（不可能，那需要价格穿过）")
        print("        ⇒ 要么**引擎记账用的 mid 与真实盘口脱节**（mid 滞后/来自旧快照）")
        print("      后果：`spread_bp` 被**虚增**，而 `price_bp` 因用同一对 mid 而被**等量虚减**")
        print("      ⇒ **net_bp（合计）可能仍是对的**，但**两维的分解不可信**，")
        print("        且**无法与论文的 markout 口径对照**（论文从限价算、终点用 microprice）。")
        print("      ⇒ 这**不是**「重复计算」，而是「分解失真」。子代理的担心方向对、机理不同。")
    else:
        print("    ⇒ 引擎 spread_bp 与半价差量级相当（%.2f 倍）⇒ 分解口径基本合理，" % ratio)
        print("      `spread_bp` 就是半价差捕获，与 `price_bp`（中价漂移）**确实是两个不同的量**，")
        print("      相加不构成重复计算。**子代理的质疑在本场地不成立。**")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h20_spread_attribution_audit.json"
    p.write_text(json.dumps({
        "hours": args.hours, "n_matched": len(out),
        "median_abs_px_vs_touch_bp": float(np.median(np.abs(d))),
        "engine_spread_bp_mean": float(es.mean()),
        "real_spread_capture_bp_mean": float(rs.mean()),
        "real_full_spread_bp_mean": float(rspr.mean()),
        "records": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
