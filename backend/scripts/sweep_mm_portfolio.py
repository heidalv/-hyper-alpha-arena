# -*- coding: utf-8 -*-
"""[F75 第十三轮] 组合级共享账本参数扫描（真实口径）。

单币扫描 +4.6bp 的乐观偏差来自跨币共享敞口竞争；本扫描用 portfolio_replay
（共享 InventoryBook + 合并时间线）找真实正收益配置。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import replay_portfolio, _load_all  # noqa: E402

OUT = ROOT / "data" / "mm_portfolio_sweep.json"
SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE"]
EQUITY = 5000.0

GRID = [
    # (w, k, hold, fill_notional, exposure, directional)
    (8.0, 0.6, 900.0, 100.0, 0.06, 0.04),  # 当前生产配置（参考行）
    (8.0, 0.6, 300.0, 100.0, 0.06, 0.04),
    (8.0, 0.6, 300.0, 50.0, 0.06, 0.04),
    (8.0, 0.6, 900.0, 50.0, 0.06, 0.04),
    (8.0, 1.0, 300.0, 50.0, 0.06, 0.04),
    (8.0, 1.0, 900.0, 50.0, 0.06, 0.04),
    (8.0, 0.6, 300.0, 100.0, 0.12, 0.08),
    (8.0, 0.6, 900.0, 100.0, 0.12, 0.08),
    (8.0, 1.0, 300.0, 100.0, 0.12, 0.08),
    (5.0, 1.0, 300.0, 100.0, 0.12, 0.08),
    (5.0, 1.0, 300.0, 50.0, 0.12, 0.08),
    (5.0, 1.0, 900.0, 50.0, 0.12, 0.08),
    (8.0, 1.0, 300.0, 50.0, 0.12, 0.08),
    (5.0, 0.6, 300.0, 100.0, 0.12, 0.08),
    (8.0, 0.6, 300.0, 50.0, 0.18, 0.10),
    (5.0, 1.0, 300.0, 50.0, 0.18, 0.10),
]


def main() -> int:
    data = _load_all(SYMBOLS, "asterdex")
    rows = []
    for (w, k, hold, fn, expo, direc) in GRID:
        params = QuoteParams(w_base_bp=w, k_inv=k)
        limits = LaneRiskLimits(
            max_one_side_seconds=hold, stop_loss_bp=0.0, trend_pause_bp=0.0,
            vol_pause_mult=1.5, vol_pause_sigma=1.5,
            max_net_directional_ratio=direc, max_net_exposure_ratio=expo,
        )
        r = replay_portfolio(SYMBOLS, venue="asterdex", equity=EQUITY,
                             params=params, limits=limits,
                             fill_notional=fn, data=data)
        row = {
            "w": w, "k": k, "hold": hold, "fill_notional": fn,
            "exposure": expo, "directional": direc,
            "fills": r["fills"], "flatten_share": r["flatten_share"],
            "net_bp": r["net_bp"], "net_usd": round(r["net_usd"], 2),
            "max_dd_pct": r.get("max_dd_pct"),
            "positive_symbols": r.get("positive_symbols"),
            "skips": r.get("skipped"),
        }
        rows.append(row)
        print(f"w={w} k={k} hold={hold}s fn={fn} exp={expo} dir={direc} -> "
              f"fills={r['fills']} flat={r['flatten_share']} net={r['net_bp']:+.3f}bp "
              f"({r['net_usd']:+.2f} USD) dd={r.get('max_dd_pct')} pos={r.get('positive_symbols')}")

    rows_sorted = sorted(rows, key=lambda x: -(x["net_bp"] or -9))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": SYMBOLS, "equity": EQUITY, "rows": rows,
        "top": rows_sorted,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== TOP ===")
    for r in rows_sorted[:6]:
        print(r)
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
