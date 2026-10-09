"""drift_veto counterfactual analysis.

The veto log records EVERY tick where the gate fired (no write-side dedup --
the gate flag gets reassigned later in the pipeline, so write-side dedup
proved unreliable). This tool folds raw lines into discrete EVENTS and then
answers the only question that matters:

    For each vetoed entry, what would that entry have earned?

Method: for a vetoed BUY at mid m0, price at m0 is the entry reference.
The engine's normal hold window is 30-45s (measured sweet spot). So the
counterfactual P&L of the blocked entry is:

    cf_bp = (mid[t+45s] - m0) / m0 * 1e4   for a BUY
    cf_bp = (m0 - mid[t+45s]) / m0 * 1e4   for a SELL

If mean(cf_bp) < 0 -> the veto CORRECTLY blocked losing trades.
If mean(cf_bp) > 0 -> the veto blocked PROFITABLE trades (aperture shrink).
"""
from __future__ import annotations

import io
import json
import statistics as st
import sys
import time
from pathlib import Path
from collections import Counter

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
LOG = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\drift_veto_log.jsonl")
MKT = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
FOLD_SEC = 30.0      # consecutive same (symbol,side) within this window = 1 event
HOLD_SEC = 45.0      # the measured sweet-spot hold


def main() -> int:
    if not LOG.exists():
        print("no veto log yet")
        return 0
    raw = []
    for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            raw.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    print("=" * 88)
    print(f"veto log raw lines = {len(raw)}")
    print("=" * 88)
    if not raw:
        return 0

    # ── fold into events ──
    raw.sort(key=lambda d: d["ts"])
    events = []
    last = {}
    for d in raw:
        key = (d.get("symbol"), d.get("side"))
        prev = last.get(key)
        if prev is None or (d["ts"] - prev) > FOLD_SEC:
            events.append(d)
        last[key] = d["ts"]
    print(f"  folded into {len(events)} discrete veto events"
          f"  (fold window {FOLD_SEC:.0f}s)")
    print(f"  distinct symbols: {len(set(e['symbol'] for e in events))}")
    top = Counter(e["symbol"] for e in events).most_common(6)
    print(f"  top symbols: {', '.join(f'{s}={n}' for s, n in top)}")

    # ── counterfactual using real book prices ──
    print()
    print("=" * 88)
    print(f"counterfactual: what would each blocked entry have earned "
          f"over {HOLD_SEC:.0f}s?")
    print("=" * 88)
    rows = []
    with psycopg.connect(MKT, autocommit=True) as m, m.cursor() as mc:
        for e in events:
            sym = str(e["symbol"]).upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            m0 = float(e.get("mid") or 0.0)
            if m0 <= 0:
                continue
            t0 = int(float(e["ts"]) * 1000)
            t1 = t0 + int(HOLD_SEC * 1000)
            mc.execute("""
                SELECT ((bid_px+ask_px)/2.0) FROM asterdex_book_ticker
                WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
                ORDER BY event_ts_ms DESC LIMIT 1
            """, (sym, t1 - 15000, t1 + 15000))
            r = mc.fetchone()
            if not r or r[0] is None or float(r[0]) <= 0:
                continue
            m1 = float(r[0])
            cf = ((m1 - m0) / m0 * 1e4) if e["side"] == "buy" else ((m0 - m1) / m0 * 1e4)
            rows.append((e["symbol"], e["side"], e.get("trend10_bp"), cf))

    if len(rows) < 5:
        print(f"  only {len(rows)} events with follow-up price -- too few")
        return 0

    cfs = [r[3] for r in rows]
    mu, sd, n = st.mean(cfs), st.pstdev(cfs), len(cfs)
    se = sd / (n ** 0.5) if n else 0
    t = mu / se if se else 0.0
    print(f"  n = {n}")
    print(f"  counterfactual mean = {mu:+.2f} bp  (sd {sd:.1f})")
    print(f"  SE = {se:.2f}   **t = {t:+.2f}**")
    print(f"  95% CI = [{mu-1.96*se:+.2f}, {mu+1.96*se:+.2f}]")
    print(f"  win rate = {sum(1 for x in cfs if x>0)/n*100:.0f}%")
    print()
    print(f"  {'symbol':<12}{'side':<6}{'trend10':>10}{'cf_bp':>10}")
    for r in sorted(rows, key=lambda x: -abs(x[3]))[:8]:
        print(f"  {str(r[0])[:11]:<12}{str(r[1]):<6}{float(r[2] or 0):>10.1f}"
              f"{r[3]:>10.2f}")
    print()
    if mu < 0:
        print("  => blocked entries would have LOST on average")
        print("     => drift_veto has evidence (it blocked losers) -- KEEP")
    else:
        print("  => blocked entries would have WON on average")
        print("     => drift_veto shrank the aperture without evidence -- REVIEW")
    if abs(t) < 1.96:
        print(f"     (but |t|={abs(t):.2f} < 1.96 -> not statistically conclusive yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
