"""H35：同一套特征在三所上的预测力对比（Aster vs Binance vs Hyperliquid）。

## 为什么这是回答"换个所会怎样"最直接的一步

用户问：「现在都是在 Aster 上，币安上做会是怎样？HL 上或者其他所？」

要回答它，**必须先分清两件事**：
  (A) **预测力**（IC）在各所是否一样 —— 这决定"信号能不能用"
  (B) **执行经济**（捕获率、逆向选择、费率）在各所是否一样 —— 这决定"用了能赚多少"

本脚本测 (A)：把 H29 的同一套特征、同一个判据，原封不动地跑在
`market_trades_aggregated` 的三个 exchange 上。

**我们确实有三所的真实数据**（`diag_multivenue_coverage.py` 实测）：
    binance      103 万行 / 23 币 / 722h
    hyperliquid   36 万行 / 71 币 / 722h
    asterdex      35 万行 / 23 币 / 273h

## 口径纪律（与 H29 完全一致，便于横向比较）

  · 同一套 23 个特征（H29 的 `build_features`，**唯一实现，不重写**）
  · 同一组视界 {1,2,4,8,20} 桶（15s~5min）
  · 同一判据：|IC|≥0.05 且 全窗/前折/后折**三处同号** 且 ≥7/10 币同号
  · 逐 exchange 独立采样；**不跨所合并**（不同所的时间轴不对齐）

## 判据（事先定死）

  · 若某所的 IC 显著高于 Aster ⇒ 该所的信号更好用
  · 若三所 IC 大致相同 ⇒ **预测力是"市场性质"而非"交易所性质"**，
    那么差异只能来自执行经济（费率/价差/深度）—— 这是很重要的结论
  · 若某所 IC ≈ 0 ⇒ 该所不适合做这个策略

用法：
    .venv\\Scripts\\python.exe scripts\\h35_multivenue_ic.py --hours 240
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

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


COLS = ("timestamp, taker_buy_volume, taker_sell_volume,"
        " taker_buy_count, taker_sell_count,"
        " taker_buy_notional, taker_sell_notional,"
        " vwap, high_price, low_price,"
        " bid_depth_top5, ask_depth_top5, largest_trade_usd")


def main() -> int:
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h29_feature_ic_scan import HORIZONS, build_features, _spearman  # noqa: E402

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=240.0)
    ap.add_argument("--exchanges", default="asterdex,binance,hyperliquid")
    ap.add_argument("--min-buckets", type=int, default=800)
    ap.add_argument("--focus-h", type=int, default=1, help="重点视界（桶）")
    args = ap.parse_args()

    exs = [x.strip() for x in args.exchanges.split(",") if x.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("H35 三所预测力对比（同一套 23 特征 / 同一判据）")
    print(f"窗口={args.hours}h  交易所={exs}\n")

    all_res = {}
    for ex in exs:
        cur.execute(
            "SELECT symbol, COUNT(*) n FROM market_trades_aggregated"
            " WHERE exchange=%s AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " GROUP BY symbol HAVING COUNT(*) >= %s ORDER BY n DESC",
            (ex, int(args.hours * 3600_000), args.min_buckets))
        syms = [r["symbol"] for r in cur.fetchall()]
        if not syms:
            print(f"--- {ex}: 无足够数据 ---\n")
            continue

        print(f"--- {ex}  ({len(syms)} 币) ---")
        per = {}
        for s in syms:
            cur.execute(
                f"SELECT {COLS} FROM market_trades_aggregated"
                " WHERE exchange=%s AND symbol=%s"
                "   AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
                " ORDER BY timestamp",
                (ex, s, int(args.hours * 3600_000)))
            rows = cur.fetchall()
            d = build_features(rows)
            if d:
                per[s] = d
        if not per:
            print("   （特征构造失败）\n")
            continue

        names = sorted(next(iter(per.values()))["F"].keys())
        res = defaultdict(lambda: defaultdict(list))
        for s, d in per.items():
            F, fwd, n = d["F"], d["fwd"], d["n"]
            half = n // 2
            for nm in names:
                x = F[nm]
                for h in HORIZONS:
                    y = fwd[h]
                    ic, nn = _spearman(x, y)
                    ic1, _ = _spearman(x[:half], y[:half])
                    ic2, _ = _spearman(x[half:], y[half:])
                    if np.isfinite(ic):
                        res[nm][h].append((s, ic, ic1, ic2, nn))

        # 汇总重点视界
        h = args.focus_h
        rows_out = []
        for nm in names:
            v = res[nm][h]
            if not v:
                continue
            ics = np.array([x[1] for x in v])
            ic1 = np.array([x[2] for x in v if np.isfinite(x[2])])
            ic2 = np.array([x[3] for x in v if np.isfinite(x[3])])
            same = max(int((ics > 0).sum()), int((ics < 0).sum()))
            sign_ok = bool(len(ic1) and len(ic2)
                           and np.sign(ic1.mean()) == np.sign(ics.mean())
                           and np.sign(ic2.mean()) == np.sign(ics.mean()))
            se = ics.std(ddof=1) / np.sqrt(len(ics)) if len(ics) > 1 else 0.0
            rows_out.append({
                "feature": nm, "ic": float(ics.mean()),
                "t": float(ics.mean() / se) if se > 0 else 0.0,
                "n_sym": len(v), "same_sign": same,
                "ic_fold1": float(ic1.mean()) if len(ic1) else None,
                "ic_fold2": float(ic2.mean()) if len(ic2) else None,
                "folds_same_sign": sign_ok,
                "passes": bool(sign_ok and abs(ics.mean()) >= 0.05
                               and same >= max(7, int(0.7 * len(v)))),
            })
        rows_out.sort(key=lambda r: -abs(r["ic"]))
        all_res[ex] = {"n_symbols": len(per), "h": h, "rows": rows_out}

        print("     %-18s %9s %8s %8s %10s %6s" %
              ("feature", "IC", "t", "前折", "后折", "过判据"))
        for r in rows_out[:12]:
            f = lambda v: ("%+8.4f" % v) if v is not None else "       —"
            print("     %-18s %+9.4f %+8.2f %s %s %6s"
                  % (r["feature"], r["ic"], r["t"], f(r["ic_fold1"]),
                     f(r["ic_fold2"]), "✓" if r["passes"] else ""))
        np_ = sum(1 for r in rows_out if r["passes"])
        print(f"     ⇒ 通过判据的特征数：{np_} / {len(rows_out)}\n")

    # ── 跨所对照 ────────────────────────────────────────────────
    print("=" * 78)
    print(f"[跨所对照] 重点视界 h={args.focus_h}（{args.focus_h*15}s）；只列三所都有的特征")
    h = args.focus_h
    common = None
    for ex in all_res:
        fs = {r["feature"] for r in all_res[ex]["rows"]}
        common = fs if common is None else (common & fs)
    if not common:
        print("  无可比特征")
    else:
        print("     %-18s %s" % ("feature", "".join("%14s" % e for e in all_res)))
        summ = {}
        for nm in sorted(common):
            line = "     %-18s" % nm
            for ex in all_res:
                r = next((x for x in all_res[ex]["rows"] if x["feature"] == nm), None)
                line += "%14s" % (("%+.4f" % r["ic"]) if r else "—")
                if r and ex == "asterdex":
                    summ[nm] = r["ic"]
            print(line)
        # 与 Aster 的一致性
        print("\n     [一致性] 各所 IC 与 Aster 的符号是否相同 + 均值")
        for ex in all_res:
            if ex == "asterdex":
                continue
            ok = tot = 0
            ics = []
            for nm in sorted(common):
                ra = next((x for x in all_res["asterdex"]["rows"] if x["feature"] == nm), None)
                rb = next((x for x in all_res[ex]["rows"] if x["feature"] == nm), None)
                if not ra or not rb:
                    continue
                tot += 1
                ics.append(rb["ic"])
                if np.sign(ra["ic"]) == np.sign(rb["ic"]):
                    ok += 1
            if tot:
                print("       %-14s 符号一致 %2d/%2d   IC 均值 %+.4f"
                      % (ex, ok, tot, float(np.mean(ics))))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h35_multivenue_ic.json"
    p.write_text(json.dumps({"hours": args.hours, "focus_h": args.focus_h,
                             "by_exchange": all_res},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
