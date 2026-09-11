# -*- coding: utf-8 -*-
"""做市参数扫描（第十一轮续）：找能把净边际翻正的配置。

调用 `market_maker.replay.replay_symbol`（与影子跑同一引擎），
扫描：
  - w_base_bp  （基础挂单距离）
  - k_inv      （库存偏斜系数）
  - max_one_side_seconds（单边持仓上限 → 影响超时平仓频率）
  - stop_loss_bp（浮亏止损）

数据：asterdex 全量盘口快照 + 区间成交（回放窗口 30 天）。
用法：.venv\\Scripts\\python.exe backend/scripts/sweep_mm_params.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.replay import replay_symbol, _load_series  # noqa: E402

OUT = ROOT / "data" / "mm_param_sweep.json"
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "UNI"]
EQUITY = 5000.0
FILL_NOTIONAL = 100.0

GRID = {
    "w_base_bp": (3.0, 5.0, 8.0),
    "k_inv": (0.3, 0.6, 1.0),
    "max_one_side_sec": (60.0, 120.0, 300.0),
    "stop_loss_bp": (10.0, 25.0, 0.0),
}


def main() -> int:
    series_cache = {}
    for s in SYMBOLS:
        try:
            series_cache[s] = _load_series(s, "asterdex", None)
        except Exception as exc:
            print(f"[WARN] {s} 数据加载失败: {str(exc)[:100]}")
            series_cache[s] = None

    rows = []
    for w in GRID["w_base_bp"]:
        for k in GRID["k_inv"]:
            for hold in GRID["max_one_side_sec"]:
                for sl in GRID["stop_loss_bp"]:
                    params = QuoteParams(w_base_bp=w, k_inv=k)
                    limits = LaneRiskLimits(
                        max_one_side_seconds=hold,
                        stop_loss_bp=sl,
                        max_net_directional_ratio=0.10,
                    )
                    agg_notional = 0.0
                    agg_net = 0.0
                    agg_fills = 0
                    per = {}
                    for s in SYMBOLS:
                        if series_cache.get(s) is None:
                            continue
                        res = replay_symbol(
                            s, venue="asterdex", equity=EQUITY, params=params,
                            limits=limits, series=series_cache[s],
                            fill_notional=FILL_NOTIONAL, n_folds=6, return_fills=False,
                        )
                        per[s] = {
                            "net_bp": round(res.net_bp, 3),
                            "fills": res.fills,
                            "flatten_share": (round(res.flattens / res.fills, 3)
                                              if res.fills else None),
                            "avg_hold_snaps": res.avg_hold_snapshots,
                        }
                        agg_notional += res.notional
                        agg_net += res.net_usd
                        agg_fills += res.fills
                    net_bp = agg_net / agg_notional * 1e4 if agg_notional > 0 else 0.0
                    rows.append({
                        "w_base_bp": w, "k_inv": k, "max_one_side_sec": hold,
                        "stop_loss_bp": sl, "fills": agg_fills,
                        "net_bp": round(net_bp, 3),
                        "net_usd": round(agg_net, 4),
                        "per_symbol": per,
                    })
                    print(f"w={w} k={k} hold={hold}s sl={sl} -> fills={agg_fills} "
                          f"net={net_bp:+.3f}bp ({agg_net:+.2f} USD)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "equity": EQUITY, "fill_notional": FILL_NOTIONAL,
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    pos = [r for r in rows if r["net_bp"] > 0]
    pos.sort(key=lambda x: -x["net_bp"])
    print(f"\n净边际 > 0 的配置: {len(pos)} / {len(rows)}")
    for r in pos[:15]:
        print(f"  [OK] w={r['w_base_bp']} k={r['k_inv']} hold={r['max_one_side_sec']}s "
              f"sl={r['stop_loss_bp']} -> {r['net_bp']:+.3f}bp ({r['fills']} 笔)")
    if not pos:
        print("[WARN] 所有配置均为负 —— 现有引擎+费率假设下无解。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
