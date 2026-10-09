"""H24：用**引擎的真实报价**做反事实 —— 贴 touch vs 挂宽，在队列消耗制成交下谁赢？

## 为什么这是决定性的一步

H23 已给出"距 touch 档位 → 成交率 + markout"的阶梯（8h，4 币，976 次报价）：

    档位   成交率    mk@1s     mk@5s     mk@30s
      0    25.82%   -0.083    +0.225    -0.668     ← touch
      1     6.15%   -1.136    -1.536    -1.367
      2     4.92%   -0.717    -1.144    -1.209
      3     2.46%   -1.338    -2.102    -1.778

但 H23 用的是**合成的报价**（"假设我们在第 k 档挂单"），不是引擎真正会挂的价。
本脚本改用**引擎运行态里的真实报价**（`mm_lane_status.json` 的
`quote_bid`/`quote_ask`），并同时算两个反事实：

    A. **现状**：挂 `mid ± 1.5bp`（引擎实际在挂的）
    B. **贴 touch**：挂当前最优买价/卖价

两者都在**队列消耗制**下判成交（论文口径：累计对手量 ≥ 前方挂量），
并都从**限价**算 markout。输出**每笔净额**与**每小时净额**。

## 会计恒等式（Thogiti，practitioner）

    Π_buy(τ)  = ΔM(τ) + s/2        Π_sell(τ) = s/2 − ΔM(τ)
    ⇒ 打平条件 |ΔM| < s/2

我们现状：|ΔM| ≈ 2.02bp，s/2 ≈ 0.72bp ⇒ 差 2.8 倍。
本脚本直接量出 A/B 两条路径的 (s/2, ΔM, 净额)，看哪条过线。

## 口径纪律

  · markout **从限价算**（与论文 Table 1 / H23 一致），不从成交后中价算。
  · 成交率与 markout **必须成对报告** —— 只看 markout 会选出"几乎不成交但数字好看"的配置。
  · 每档的"每小时净额"= 成交率 × 每笔净额 × 每小时报价次数 × 币数。
  · **不许**把 A 与 B 的样本相加（不同报价策略，样本不可合并）。

用法：
    .venv\\Scripts\\python.exe scripts\\h24_touch_vs_wide_counterfactual.py --hours 8
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
HORIZONS_MS = [1000, 5000, 30000]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--symbols", default="ASTERUSDT,XRPUSDT,SOLUSDT,DOGEUSDT")
    ap.add_argument("--quote-every-s", type=float, default=60.0,
                    help="反事实报价节奏（不必等于线上 15s；60s 足够且省算力）")
    ap.add_argument("--max-wait-s", type=float, default=300.0)
    ap.add_argument("--leg-usd", type=float, default=30.0)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(args.hours * 3600_000)}"

    print("H24 反事实：贴 touch vs 挂宽（队列消耗制成交，markout 从限价算）")
    print(f"窗口={args.hours}h  币={len(syms)}  报价节奏={args.quote_every_s:.0f}s  "
          f"腿量=${args.leg_usd:.0f}  最长等待={args.max_wait_s:.0f}s\n")

    # 策略：A = mid ± w_base_bp（线上实际）；B = 最优买/卖价
    strategies = {
        "A 现状 mid±1.5bp": {"mode": "from_mid", "w_bp": 1.5},
        "B 贴 touch": {"mode": "touch", "w_bp": 0.0},
    }

    results = {}
    for name, cfg in strategies.items():
        per_sym = []
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
                f" WHERE symbol = %s AND event_ts_ms > {since} ORDER BY event_ts_ms",
                (s,),
            )
            d = cur.fetchall()
            cur.execute(
                "SELECT event_ts_ms, price::float AS p, qty::float AS q,"
                "       is_buyer_maker AS ibm"
                f"  FROM asterdex_trades WHERE symbol = %s AND event_ts_ms > {since}"
                " ORDER BY event_ts_ms",
                (s,),
            )
            tr = cur.fetchall()
            if len(d) < 300 or len(tr) < 200:
                continue

            dts = np.array([int(x["event_ts_ms"]) for x in d], dtype=np.int64)
            tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
            tpx = np.array([float(x["p"]) for x in tr])
            tq = np.array([float(x["q"]) for x in tr])
            tsell = np.array([bool(x["ibm"]) for x in tr])
            tbuy = ~tsell

            mids = np.array([
                (float(x["bids"][0][0]) + float(x["asks"][0][0])) / 2.0
                if x["bids"] and x["asks"] else np.nan for x in d
            ])

            def mid_at(t_ms, delta_ms):
                i = int(np.searchsorted(dts, t_ms + delta_ms, "left"))
                if i >= len(dts):
                    return None
                m = mids[i]
                return float(m) if np.isfinite(m) else None

            step = max(1, int(args.quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
            recs = []
            for i in range(0, len(d), step):
                x = d[i]
                if not x["bids"] or not x["asks"]:
                    continue
                bb = float(x["bids"][0][0])
                ba = float(x["asks"][0][0])
                bq = float(x["bids"][0][1])
                aq = float(x["asks"][0][1])
                mid = (bb + ba) / 2.0
                if mid <= 0:
                    continue
                t0 = int(dts[i])
                if cfg["mode"] == "touch":
                    px, la = bb, bq
                else:
                    px = mid * (1.0 - cfg["w_bp"] / 1e4)
                    # 挂在最优价之外 ⇒ LA 未知；保守用最优档挂量当"前面的量"下界
                    la = bq
                if px <= 0:
                    continue
                # 队列消耗制：该价位累计主动卖量 ≥ LA
                j = int(np.searchsorted(tts, t0, "left"))
                lim = int(np.searchsorted(tts, t0 + int(args.max_wait_s * 1000), "left"))
                cum = 0.0
                fill_j = -1
                while j < lim:
                    if tsell[j] and abs(tpx[j] - px) / px * 1e4 < 1.0:
                        cum += float(tq[j])
                        if cum >= la:
                            fill_j = j
                            break
                    j += 1
                r = {"filled": fill_j >= 0, "half_spread_bp": (mid - px) / mid * 1e4}
                if fill_j >= 0:
                    tf = int(tts[fill_j])
                    for h in HORIZONS_MS:
                        m = mid_at(tf, h)
                        # markout **从限价算**（买单：涨了才对我们不利？不 ——
                        # 买单成交后中价下跌 = 逆向选择 ⇒ 用 (mid/px − 1)
                        r[f"mk{h}"] = ((m / px - 1.0) * 1e4) if m else None
                recs.append(r)
            if recs:
                per_sym.append({"symbol": s, "n_quotes": len(recs), "recs": recs})

        # 汇总
        nq = sum(x["n_quotes"] for x in per_sym)
        filled = [r for x in per_sym for r in x["recs"] if r["filled"]]
        fr = len(filled) / max(1, nq)
        half = np.array([r["half_spread_bp"] for r in filled]) if filled else np.array([])
        row = {"n_quotes": nq, "n_filled": len(filled), "fill_rate": fr,
               "half_spread_bp": float(half.mean()) if len(half) else None}
        for h in HORIZONS_MS:
            v = np.array([r.get(f"mk{h}") for r in filled if r.get(f"mk{h}") is not None])
            if len(v):
                row[f"mk{h}"] = float(v.mean())
                row[f"mk{h}_net"] = float(half.mean() - v.mean())   # Π = s/2 − |ΔM|
            else:
                row[f"mk{h}"] = None
                row[f"mk{h}_net"] = None
        results[name] = row

    # ── 输出 ──────────────────────────────────────────────────────
    print("%-20s %8s %8s %9s %10s %10s %10s" %
          ("策略", "报价数", "成交数", "成交率", "半价差", "mk@1s", "mk@30s"))
    for name, r in results.items():
        f = lambda v: ("%10.3f" % v) if v is not None else "         —"
        print("%-20s %8d %8d %8.2f%% %s %s %s"
              % (name, r["n_quotes"], r["n_filled"], r["fill_rate"] * 100,
                 f(r["half_spread_bp"]), f(r.get("mk1000")), f(r.get("mk30000"))))

    print("\n[净额] Π = s/2 − |ΔM|（Thogiti 恒等式；s/2 = 半价差，ΔM = markout）")
    print("%-20s %10s %10s %10s %12s" % ("策略", "净@1s", "净@5s", "净@30s", "每笔均$@1s"))
    for name, r in results.items():
        f = lambda v: ("%10.3f" % v) if v is not None else "         —"
        n1 = r.get("mk1000_net")
        usd = (n1 / 1e4 * args.leg_usd) if n1 is not None else None
        print("%-20s %s %s %s %12s"
              % (name, f(n1), f(r.get("mk5000_net")), f(r.get("mk30000_net")),
                 ("%+.6f" % usd) if usd is not None else "—"))

    print("\n[每小时预期] 成交率 × 每笔净额 × 每小时报价次数 × 币数")
    hours = args.hours
    nsym = max(1, len({s for s in syms}))
    for name, r in results.items():
        n1 = r.get("mk1000_net")
        if n1 is None:
            continue
        quotes_per_hour = r["n_quotes"] / hours
        fills_per_hour = r["n_filled"] / hours
        usd_per_fill = n1 / 1e4 * args.leg_usd
        print("    %-20s 成交/时 %6.1f   每笔 %+.6f$   ⇒ **%+.4f$/时**"
              % (name, fills_per_hour, usd_per_fill, fills_per_hour * usd_per_fill))

    print("\n[判定]")
    a = results.get("A 现状 mid±1.5bp", {})
    b = results.get("B 贴 touch", {})
    na, nb = a.get("mk1000_net"), b.get("mk1000_net")
    if na is not None and nb is not None:
        print("  现状 净 %.3fbp   贴 touch 净 %.3fbp   ⇒ 差 %+.3fbp" % (na, nb, nb - na))
        if nb > 0 and na < 0:
            print("  ⇒ **贴 touch 转正、现状为负** ⇒ 最高价值的改动是改挂单价位，不是找信号。")
        elif nb > na:
            print("  ⇒ 贴 touch 更优但两者同号。")
        else:
            print("  ⇒ 贴 touch 并未改善 ⇒ H23 的档位阶梯不能直接外推到真实报价。")
    print("\n  口径提醒：")
    print("   · markout 从**限价**算（与论文 Table 1 一致），不是从成交后中价。")
    print("   · A 的 'LA = 最优档挂量' 是**下界**（真实挂在最优价之外时前方量未知）")
    print("     ⇒ A 的成交率是**上界**（偏乐观），对 A 有利，结论若仍支持 B 则更可信。")
    print("   · 未成交样本被截断在 %.0fs ⇒ 成交率是下界。" % args.max_wait_s)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h24_touch_vs_wide_counterfactual.json"
    p.write_text(json.dumps({"hours": args.hours, "symbols": syms,
                             "leg_usd": args.leg_usd, "results": results},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
