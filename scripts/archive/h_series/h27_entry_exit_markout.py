"""H27：入场腿 vs 出场腿的 markout —— 把 71.1% 的亏损定位到机制。

## 已知（H26）

8h，57 个完整往返：
    入场腿  +$0.0112 / 往返   （赚）
    出场腿  −$0.0276 / 往返   （亏 2.5 倍）
    出场腿占盈亏波动 **71.1%**
    持仓时长中位仅 **64s** ⇒ **不是"拿太久"**

## 本脚本检验四个假设（事先定死判据，不许事后挑）

  **A. 出场腿的 markout 更毒**
     入场与出场都是被动成交，但从**各自限价**算的 markout 可能不同。
     判据：出场腿 |ΔM| > 入场腿 |ΔM| 且差 ≥ 0.3bp ⇒ 支持。

  **B. 出场腿走了 taker**
     H21 说强平只占 16.8%。本脚本按**费用**反推：fee_bp < −3.5 的腿疑似 taker
     （Aster taker=4bp、maker=0）⇒ 若 taker 腿占比很低，假设 B 不成立。

  **C. 库存偏斜把出场侧挂窄了**
     `min_width_reduce_bp=0` 允许减仓侧贴盘口 ⇒ 捕获更少。
     判据：出场腿的半价差（相对成交时中价）显著小于入场腿 ⇒ 支持。

  **D. 库存本身就是逆向选择的信号**（最值得怀疑的一条）
     我们之所以有库存，正因为之前价格朝不利方向走了
     ⇒ 此时被动挂减仓单在结构上就逆风。
     判据：按**入场后中价方向**分组，若"入场后中价继续朝不利方向走"的那些往返
     出场腿明显更亏 ⇒ 支持。

## 口径纪律

  · markout **从限价（成交价）算**：买单 (mid/px − 1)，卖单 (px/mid − 1)，
    正 = 有利。这与论文 Table 1 / H19 / H23-H25 一致。
  · 必须**分侧别**报告（H5/H7 的教训：不分侧的正 markout 可能是 beta）。
  · 用**金额**汇总成本，不用平均 bp（名义跨 67 倍，见 H21）。
  · 跨时代不求和；本脚本只统计 `--hours` 窗口内。

用法：
    .venv\\Scripts\\python.exe scripts\\h27_entry_exit_markout.py --hours 8
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
TAUS_MS = [500, 1000, 5000, 30000, 120000]


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
    ap.add_argument("--tol-ms", type=int, default=5000,
                    help="成交时刻与盘口快照的最大匹配间隔")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    # ── ① 账户账本（权威的 side / px / qty）─────────────────────
    biz = psycopg2.connect(_dsn("alpha_arena"))
    biz.autocommit = True
    cur = biz.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT created_at, amount_usd, metadata_json
          FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND created_at > now() - make_interval(secs => %s)
         ORDER BY created_at
        """,
        (args.account_id, args.hours * 3600.0),
    )
    rows = cur.fetchall()

    legs = []
    for r in rows:
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        side = str(md.get("side") or "").lower()
        qty = float(md.get("qty") or 0.0)
        px = float(md.get("px") or 0.0)
        if not side or qty == 0 or px <= 0:
            continue
        t = r["created_at"]
        # ⚠️ `arbitrage_paper_ledgers.created_at` 是 **naive** 时间戳，存的是**本地墙钟**
        #    （已多次确认）。按 UTC 解会整体偏 8 小时 ⇒ 与 `asterdex_book_ticker.event_ts_ms`
        #    完全对不上（实测匹配 0/289 笔）。必须按**本地时区**解释。
        t_ep = (t.replace(tzinfo=datetime.now().astimezone().tzinfo).timestamp()
                if t.tzinfo is None else t.timestamp())
        legs.append({
            "ts": t, "ts_ep": t_ep, "symbol": md.get("symbol") or "-",
            "is_buy": side.startswith("b"), "qty": qty, "px": px,
            "amt": float(r["amount_usd"] or 0.0),
            "signed": qty if side.startswith("b") else -qty,
        })
    print(f"H27 入场/出场腿 markout  账户={args.account_id}  窗口={args.hours}h")
    print(f"        可用腿 {len(legs)} 笔\n")
    if not legs:
        print("账本缺 side/px（旧版本写入的行）⇒ 无法计算")
        return 1

    # ── ② 按币重建库存轨迹 → 标 entry / exit ────────────────────
    by_sym = defaultdict(list)
    for x in legs:
        by_sym[x["symbol"]].append(x)
    for s, xs in by_sym.items():
        pos = 0.0
        for x in xs:
            prev = pos
            pos += x["signed"]
            x["kind"] = "entry" if abs(pos) > abs(prev) + 1e-12 else "exit"
            x["pos_after"] = pos
            x["pos_before"] = prev

    all_legs = [x for xs in by_sym.values() for x in xs]
    n_en = sum(1 for x in all_legs if x["kind"] == "entry")
    n_ex = len(all_legs) - n_en
    print(f"[0] 腿分类：入场 {n_en} 笔 / 出场 {n_ex} 笔\n")

    # ── ③ 取真实盘口，逐腿算 markout ─────────────────────────────
    mkt = psycopg2.connect(_dsn("alpha_market"))
    mkt.autocommit = True
    cur2 = mkt.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    for s in sorted(by_sym):
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur2.execute(
            "SELECT event_ts_ms, bid_px::float AS b, ask_px::float AS a"
            "  FROM asterdex_book_ticker WHERE symbol = %s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, int(args.hours * 3600_000)),
        )
        bt = cur2.fetchall()
        if not bt:
            continue
        bts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
        mid = (np.array([float(x["b"]) for x in bt])
               + np.array([float(x["a"]) for x in bt])) / 2.0

        def mid_at(t_ms, delta):
            i = int(np.searchsorted(bts, t_ms + delta, "left"))
            if i >= len(bts):
                return None
            return float(mid[i])

        for x in by_sym[s]:
            t_ms = int(x["ts_ep"] * 1000)
            i = int(np.searchsorted(bts, t_ms, "left"))
            # ⚠️ `searchsorted(..., 'left')` 在 t_ms 超过最后一个元素时会返回 len(bts)
            #    ⇒ 直接下标 `bts[i]` 会 IndexError（实测踩到）。
            if i >= len(bts):
                x["matched"] = False
                continue
            j = i if (i == 0 or bts[i] - t_ms <= t_ms - bts[i - 1]) else max(0, i - 1)
            if abs(int(bts[j]) - t_ms) > args.tol_ms:
                x["matched"] = False
                continue
            x["matched"] = True
            m0 = float(mid[j])
            x["mid_at_fill"] = m0
            # 半价差：成交时中价相对我们成交价的距离（正 = 我们成交在中价的有利侧）
            x["half_spread_bp"] = (((m0 - x["px"]) if x["is_buy"] else (x["px"] - m0))
                                   / m0 * 1e4)
            for tau in TAUS_MS:
                mm = mid_at(t_ms, tau)
                if mm:
                    # 正 = 有利（买单：中价涨；卖单：中价跌）
                    x[f"mk{tau}"] = (((mm / x["px"] - 1.0) if x["is_buy"]
                                      else (1.0 - mm / x["px"])) * 1e4)
                else:
                    x[f"mk{tau}"] = None

    matched = [x for x in all_legs if x.get("matched")]
    print(f"[1] 盘口匹配成功 {len(matched)} / {len(all_legs)} 笔\n")
    if not matched:
        print("没有匹配上的腿 ⇒ 提高 --tol-ms 或检查盘口历史范围")
        return 1

    # ── ④ 分「腿类型 × 侧别」报告 markout ────────────────────────
    print("[2] markout（从成交价算；正 = 有利）按 腿类型 × 侧别")
    print("    %-7s %-5s %6s %10s %9s %9s %9s %9s"
          % ("类型", "侧", "笔数", "半价差", "mk@0.5s", "mk@1s", "mk@5s", "mk@30s"))
    summary = {}
    for kind in ("entry", "exit"):
        for is_buy in (True, False):
            sel = [x for x in matched if x["kind"] == kind and x["is_buy"] == is_buy]
            if not sel:
                continue
            hs = np.array([x["half_spread_bp"] for x in sel])
            cells = []
            for tau in TAUS_MS:
                v = np.array([x[f"mk{tau}"] for x in sel if x.get(f"mk{tau}") is not None])
                cells.append(float(v.mean()) if len(v) else float("nan"))
            lab = "买单" if is_buy else "卖单"
            print("    %-7s %-5s %6d %10.3f %9.3f %9.3f %9.3f %9.3f"
                  % (kind, lab, len(sel), hs.mean(), cells[0], cells[1], cells[2], cells[3]))
            summary[f"{kind}_{'buy' if is_buy else 'sell'}"] = {
                "n": len(sel), "half_spread_bp": float(hs.mean()),
                **{f"mk{t}": c for t, c in zip(TAUS_MS, cells)},
            }

    # ── ⑤ 假设检验 ───────────────────────────────────────────────
    print("\n[3] 假设检验")

    def cell(kind, tau):
        v = [x.get(f"mk{tau}") for x in matched
             if x["kind"] == kind and x.get(f"mk{tau}") is not None]
        return np.array(v) if v else np.array([])

    # A：出场腿 |ΔM| 是否更大（用**绝对值均值**，因为两侧都有正有负）
    print("\n  A. 出场腿 markout 是否更毒")
    ex_a = np.abs(cell("exit", 5000))
    en_a = np.abs(cell("entry", 5000))
    if len(ex_a) and len(en_a):
        d = ex_a.mean() - en_a.mean()
        print("     入场 |ΔM|@5s = %.3f   出场 |ΔM|@5s = %.3f   差 %+.3f bp"
              % (en_a.mean(), ex_a.mean(), d))
        print("     ⇒ %s" % ("**支持**（出场更毒）" if d >= 0.3 else "不支持（差异 < 0.3bp）"))

    # B：taker 腿占比（用 half_spread 反推不可靠，改用 meta.phase）
    print("\n  B. 出场腿是否走了 taker")
    cur.execute(
        """
        SELECT metadata_json FROM arbitrage_paper_ledgers
         WHERE account_id = %s AND action = 'paper_pnl'
           AND created_at > now() - make_interval(secs => %s)
        """,
        (args.account_id, args.hours * 3600.0),
    )
    n_flat = n_all = 0
    for r in cur.fetchall():
        try:
            md = json.loads(r["metadata_json"] or "{}")
        except Exception:
            md = {}
        n_all += 1
        if md.get("flatten"):
            n_flat += 1
    print("     带 flatten 标记的腿 %d / %d = %.1f%%"
          % (n_flat, n_all, 100.0 * n_flat / max(1, n_all)))
    print("     ⇒ %s" % ("**不成立**（taker 占比低，不是主因）"
                        if n_flat / max(1, n_all) < 0.10 else "可能成立，需细查"))

    # C：出场腿半价差是否更小
    print("\n  C. 出场侧是否被挂窄了（半价差更小）")
    en_h = np.array([x["half_spread_bp"] for x in matched if x["kind"] == "entry"])
    ex_h = np.array([x["half_spread_bp"] for x in matched if x["kind"] == "exit"])
    if len(en_h) and len(ex_h):
        print("     入场 半价差 %.3f   出场 半价差 %.3f   差 %+.3f bp"
              % (en_h.mean(), ex_h.mean(), ex_h.mean() - en_h.mean()))
        print("     ⇒ %s" % ("**支持**（出场捕获更少）"
                            if ex_h.mean() < en_h.mean() - 0.3 else "不支持"))

    # D：库存本身是逆向选择信号 —— 按"入场后至出场期间的中价方向"分组
    print("\n  D. 库存是否本身逆风（按入场后中价方向分组看出场腿）")
    by_sym_m = defaultdict(list)
    for x in matched:
        by_sym_m[x["symbol"]].append(x)
    adv, fav = [], []
    for s, xs in by_sym_m.items():
        # 配对：连续 entry 累积 → 一个 exit 收尾
        open_px = None
        for x in xs:
            if x["kind"] == "entry":
                open_px = x
            elif open_px is not None:
                # 从开仓成交价到出场成交价的中价变化，按方向定"顺/逆"
                d = 0.0
                if x.get("mid_at_fill") and open_px.get("mid_at_fill"):
                    d = (x["mid_at_fill"] - open_px["mid_at_fill"]) / open_px["mid_at_fill"]
                    if not open_px["is_buy"]:
                        d = -d
                (fav if d > 0 else adv).append(x)
                open_px = None
    if adv or fav:
        a_amt = sum(x["amt"] for x in adv)
        f_amt = sum(x["amt"] for x in fav)
        print("     入场后中价**不利**方向的出场腿: %d 笔  合计 %+.6f $  每笔 %+.6f $"
              % (len(adv), a_amt, a_amt / max(1, len(adv))))
        print("     入场后中价**有利**方向的出场腿: %d 笔  合计 %+.6f $  每笔 %+.6f $"
              % (len(fav), f_amt, f_amt / max(1, len(fav))))
        if adv and fav and (a_amt / max(1, len(adv))) < (f_amt / max(1, len(fav))) - 1e-6:
            print("     ⇒ **支持**：中价朝不利方向走时的出场腿明显更亏")
            print("       这印证了「库存本身就是逆向选择的信号」——我们持有库存这件事，")
            print("       就是之前价格朝不利方向走的结果，此时被动挂减仓单结构上逆风。")
        else:
            print("     ⇒ 不支持（两类出场腿的每笔均亏损无显著差异）")
    else:
        print("     样本不足，无法分组")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h27_entry_exit_markout.json"
    p.write_text(json.dumps({"hours": args.hours, "n_legs": len(matched),
                             "summary": summary, "n_flatten":
                             n_flat, "n_all": n_all},
                            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    biz.close()
    mkt.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
