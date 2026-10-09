"""Which constraint actually sets leg size?

The user said "涓嶈鎬曚簭閽卞氨寮勪粈涔堢缉灏忓彛瀛? (don't shrink the aperture out of
fear). Before changing anything, determine EMPIRICALLY which constraint binds
-- the T29 lesson was changing a knob that had no effect.

Candidate constraints, in code order:
  1. risk model  notional_cap_usd(equity, stop_bp, n, loss_frac)
  2. active_flow  cap = min(cap, equity * 0.05)        [MM_AF_LEG_CAP_PCT]
  3. probe        cap = min(cap, probe_notional_usd)   [PROBE_EQUITY_FRAC]
  4. gross        sum(notional) <= 3 * equity          [T20]
"""
from __future__ import annotations

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from backend.services.market_maker.flow_rules import notional_cap_usd  # noqa: E402
from backend.services.market_maker import flow_rules as FR  # noqa: E402

EQ = 10017.0


def main() -> int:
    print("=" * 88)
    print(f"Which constraint sets leg size?  equity = {EQ:,.0f}")
    print("=" * 88)
    print(f"  PROBE_EQUITY_FRAC            = {FR.PROBE_EQUITY_FRAC}")
    print(f"  2) equity x PROBE_EQUITY_FRAC = {EQ*FR.PROBE_EQUITY_FRAC:,.2f}"
          f"   <- observed leg size ~501")
    print()

    print("  1) risk model notional_cap_usd(equity, stop_bp, n, 0.005):")
    hdr = f"  {'stop_bp':>8}" + "".join(f"{'n=' + str(n):>13}" for n in (1, 2, 3, 5))
    print(hdr)
    for stop_bp in (10, 15, 25, 40, 60):
        cells = []
        for n in (1, 2, 3, 5):
            try:
                v = float(notional_cap_usd(EQ, stop_bp, n, 0.005))
            except TypeError:
                v = float(notional_cap_usd(EQ, stop_bp, n))
            except Exception:  # noqa: BLE001
                v = float("nan")
            cells.append(v)
        print(f"  {stop_bp:>8}" + "".join(f"{c:>13,.0f}" for c in cells))
    print()

    rm_15 = float(notional_cap_usd(EQ, 15.0, 1, 0.005))
    print(f"  => risk model at stop 15bp/1 same-side gives {rm_15:,.0f}")
    print(f"     vs observed 501  =>  ratio {rm_15/501:.1f}x")
    print()
    if rm_15 > 2 * EQ * FR.PROBE_EQUITY_FRAC:
        print("  鈬?The 5% caps (2 and 3) ARE the binding constraint, NOT the risk")
        print("     model.  This is the 'aperture shrink' the user complained about.")
    else:
        print("  鈬?The risk model itself is the binding constraint.")
    print()
    print("  4) gross cap = 3 x equity = "
          f"{3*EQ:,.0f} total exposure across all symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

