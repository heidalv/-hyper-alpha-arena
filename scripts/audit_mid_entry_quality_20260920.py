"""[2026-09-20] 中线**入场质量归因**（只读，n=近 90 天全部 mid 已平仓）。

回答的问题：51% 的单峰值从未到 +1%（完美出场天花板也只有 +1.449%/笔）——这些"从未涨过"的单
是**集中在少数来源/币种/方向上**（可操作：关掉那一路），还是**弥散在全部样本里**（说明车道本身没有 edge）？

口径：
  never1%   = peak_pnl_pct < 1.0（引擎每 30s 慢 tick 采样，故是**偏乐观**的"没涨过"判定）
  MFE/MAE   = peak_pnl_pct / trough_pnl_pct（同上，30s 采样）
  已实现%    = (close−entry)/entry × 方向
  集中度     = 亏损最大的 3 个桶占总亏损的比例（以及它们的样本占比）

用法：.venv\\Scripts\\python.exe scripts/audit_mid_entry_quality_20260920.py --days 90
"""
from __future__ import annotations

import argparse
import io
import json
import statistics as st
import sys
from typing import Dict, List

import psycopg

DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"


def load(days: str) -> List[Dict]:
    with psycopg.connect(DSN, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, entry_price, close_price, peak_pnl_pct, trough_pnl_pct,
                      exit_state_json, close_reason, opened_at,
                      coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) usd,
                      size
               from paper_positions
               where account_id=14 and status='closed' and timeframe_tier='mid'
                 and closed_at > now() - (%s || ' days')::interval
               order by opened_at""", (days,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for t in rows:
        src = None
        try:
            d = json.loads(t["exit_state_json"] or "{}")
            src = d.get("entry_source") or (d.get("open_metadata") or {}).get("entry_source")
        except Exception:
            pass
        t["src"] = src or "unknown"
        t["dir"] = "long" if str(t["side"]).lower().startswith("l") else "short"
        t["mfe"] = float(t["peak_pnl_pct"] or 0) * 100
        t["mae"] = float(t["trough_pnl_pct"] or 0) * 100
        t["real"] = ((float(t["close_price"]) - float(t["entry_price"])) / float(t["entry_price"])
                     * (1 if t["dir"] == "long" else -1)) * 100
        t["usd"] = float(t["usd"] or 0)
        t["hour"] = t["opened_at"].hour if t["opened_at"] else -1
    return rows


def table(title: str, rows: List[Dict], keyf) -> None:
    by: Dict[str, List[Dict]] = {}
    for r in rows:
        by.setdefault(str(keyf(r)), []).append(r)
    print("\n== %s ==" % title)
    print("  %-16s %-4s %-9s %-8s %-9s %-9s" % ("桶", "n", "从未+1%", "均MFE%", "均已实现%", "合计USD"))
    for k, sel in sorted(by.items(), key=lambda kv: sum(x["usd"] for x in kv[1])):
        never = sum(1 for x in sel if x["mfe"] < 1.0)
        print("  %-16s %-4d %-9s %-8.3f %-9.3f %-9.1f" %
              (k[:16], len(sel), "%d(%.0f%%)" % (never, 100 * never / len(sel)),
               st.mean([x["mfe"] for x in sel]), st.mean([x["real"] for x in sel]),
               sum(x["usd"] for x in sel)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="90")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    rows = load(a.days)
    never = [r for r in rows if r["mfe"] < 1.0]
    hit = [r for r in rows if r["mfe"] >= 1.0]
    print("样本 %d 笔（近 %s 天）" % (len(rows), a.days))
    print("  从未到 +1%%: %d 笔 (%.0f%%) 合计USD=%+.1f 均已实现=%+.3f%% 均MAE=%.3f%%"
          % (len(never), 100 * len(never) / len(rows), sum(r["usd"] for r in never),
             st.mean([r["real"] for r in never]), st.mean([r["mae"] for r in never])))
    print("  到过 +1%%:   %d 笔 (%.0f%%) 合计USD=%+.1f 均已实现=%+.3f%% 均MFE=%+.3f%%"
          % (len(hit), 100 * len(hit) / len(rows), sum(r["usd"] for r in hit),
             st.mean([r["real"] for r in hit]), st.mean([r["mfe"] for r in hit])))
    print("  整体：均MFE=%+.3f%% 均已实现=%+.3f%% 合计USD=%+.1f"
          % (st.mean([r["mfe"] for r in rows]), st.mean([r["real"] for r in rows]),
             sum(r["usd"] for r in rows)))
    table("按 entry_source", rows, lambda r: r["src"])
    table("按币种（前 10）", rows, lambda r: r["symbol"])
    table("按方向", rows, lambda r: r["dir"])
    table("按入场时段(4h)", rows, lambda r: "%02d-%02dh" % ((r["hour"] // 4) * 4, (r["hour"] // 4) * 4 + 4))
    # 集中度：亏损最大的 3 个 (source,symbol) 桶
    by: Dict[str, List[Dict]] = {}
    for r in rows:
        by.setdefault("%s|%s" % (r["src"], r["symbol"]), []).append(r)
    loss_total = abs(sum(r["usd"] for r in rows if r["usd"] < 0))
    worst = sorted(by.items(), key=lambda kv: sum(x["usd"] for x in kv[1]))[:3]
    wl = abs(sum(sum(x["usd"] for x in sel) for _k, sel in worst if sum(x["usd"] for x in sel) < 0))
    wn = sum(len(sel) for _k, sel in worst)
    print("\n== 集中度 ==")
    print("  总亏损=%.1f USD；最差 3 个 (来源|币种) 桶 = %s，合计亏损 %.1f USD (占 %.0f%%)，样本占比 %.0f%%"
          % (loss_total, [k for k, _ in worst], wl,
             (100 * wl / loss_total) if loss_total else 0, 100 * wn / len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
