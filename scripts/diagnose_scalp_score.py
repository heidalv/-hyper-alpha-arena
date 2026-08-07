"""Diagnose why scalp factor scores are low — show raw factor direction & breakdown."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "scalp_score_diagnosis.txt"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYMS = ["BTC", "ETH", "SOL", "BNB", "ASTER", "XPL", "WIF"]


def main() -> None:
    lines: list[str] = []

    def say(t: str = "") -> None:
        lines.append(t)

    from backend.config.settings import (
        SCALP_FACTOR_CONFIRM_THRESHOLD,
        SCALP_FACTOR_EXECUTE_THRESHOLD,
    )
    from backend.services.scalp_factor_router import scalp_factor_router
    from backend.services.kline_data_service import kline_service
    import pandas as pd

    say("=== scalp score diagnosis ===")
    say(f"CONFIRM_THRESHOLD={SCALP_FACTOR_CONFIRM_THRESHOLD} EXECUTE={SCALP_FACTOR_EXECUTE_THRESHOLD}")
    say("")

    for sym in SYMS:
        say(f"--- {sym} ---")
        md: dict = {}
        try:
            raw = kline_service.get_klines_from_db(sym.upper(), "5m", 100, exchange="hyperliquid")
            say(f"  5m klines: {len(raw) if raw else 0} bars")
            if raw and len(raw) >= 30:
                md["klines"] = pd.DataFrame(raw)
                md["price"] = float(raw[-1].get("close", 0) or 0)
            raw15 = kline_service.get_klines_from_db(sym.upper(), "15m", 60, exchange="hyperliquid")
            say(f"  15m klines: {len(raw15) if raw15 else 0} bars")
            if raw15 and len(raw15) > 20:
                md["klines_15m"] = pd.DataFrame(raw15)
        except Exception as e:
            say(f"  kline error: {e}")

        if "klines" not in md:
            say("  SKIP: insufficient klines")
            say("")
            continue

        # Raw factor engine
        try:
            from backend.services.factor_engine.base_factors import factor_engine
            from backend.services.factor_engine.factor_evaluation_pipeline import factor_pipeline

            fv5 = factor_engine.compute_all_factors(md["klines"], md)
            say(f"  factor count 5m: {len(fv5) if fv5 else 0}")
            if fv5:
                top = sorted(
                    ((k, v.value if hasattr(v, "value") else v) for k, v in fv5.items()),
                    key=lambda x: abs(float(x[1]) if x[1] is not None else 0),
                    reverse=True,
                )[:8]
                say("  top factors 5m:")
                for k, v in top:
                    say(f"    {k}: {v}")

            cs5 = factor_pipeline.compute_weighted_signals(fv5, md) if fv5 else None
            if cs5:
                d5 = float(cs5.direction)
                say(f"  composite 5m: direction={d5:.4f} → base_score={int(abs(d5)*100)}")
            else:
                say("  composite 5m: None")

            if md.get("klines_15m") is not None:
                fv15 = factor_engine.compute_all_factors(md["klines_15m"], md)
                cs15 = factor_pipeline.compute_weighted_signals(fv15, md) if fv15 else None
                if cs15:
                    d15 = float(cs15.direction)
                    say(f"  composite 15m: direction={d15:.4f} → score={int(abs(d15)*100)}")
                    if cs5:
                        dir5 = "long" if d5 > 0.1 else "short" if d5 < -0.1 else "neutral"
                        dir15 = "long" if d15 > 0.1 else "short" if d15 < -0.1 else "neutral"
                        if dir15 != "neutral" and dir5 == dir15:
                            say(f"  resonance: +30% → {int(abs(d5)*100*1.3)}")
                        elif dir15 != "neutral" and dir5 != dir15:
                            say(f"  resonance: -50% conflict → {int(abs(d5)*100*0.5)}")
        except Exception as e:
            say(f"  factor engine error: {e}")

        # Adaptive threshold
        try:
            thresh = scalp_factor_router._get_adaptive_threshold(sym)
            say(f"  adaptive_threshold: {thresh}")
        except Exception as e:
            say(f"  adaptive_threshold error: {e}")

        # Full router evaluate
        try:
            sig = scalp_factor_router.evaluate(sym, md)
            say(f"  router result: score={sig.factor_score} dir={sig.direction} action={sig.action}")
            say(f"  reasoning: {sig.reasoning[:120]}")
            if sig.factor_breakdown:
                say(f"  breakdown: {sig.factor_breakdown}")
        except Exception as e:
            say(f"  router error: {e}")

        # Win rate query
        try:
            from backend.database.connection import SessionLocal
            from sqlalchemy import text

            db = SessionLocal()
            row = db.execute(text("""
                SELECT count(*),
                       count(*) FILTER (WHERE unrealized_pnl > 0),
                       max(closed_at)
                FROM paper_positions
                WHERE status='closed' AND trade_nature='scalp'
                AND symbol = :sym
                AND closed_at >= NOW() - INTERVAL '3 days'
            """), {"sym": sym.upper()}).fetchone()
            db.close()
            n, wins, last = int(row[0] or 0), int(row[1] or 0), row[2]
            wr = (wins / n * 100) if n else 0
            say(f"  scalp closed 3d: n={n} wins={wins} wr={wr:.0f}% last={last}")
        except Exception as e:
            say(f"  wr query error: {e}")

        say("")

    # Score formula reminder
    say("=== score formula (since 2026-06-20) ===")
    say("score = |composite_direction| × 100  (direction in [-1,+1])")
    say("例: direction=0.26 → 26分; direction=0.08 → 8分")
    say("低分 = 因子引擎合成方向弱，不是显示 bug")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
