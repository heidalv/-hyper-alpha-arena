# -*- coding: utf-8 -*-
"""[F75 第十二轮扫描] 扩展参数扫描：把 F71 尾部闸门（止损/趋势暂停）纳入网格，
并含「当前生产配置」参考行——回答两个问题：
  1) 生产配置（w=8/k=0.6/hold=900/sl=0/trend=0）在回放里是多少净边际？（对照实盘 -3.28bp）
  2) 打开止损/趋势闸后，哪个组合在 asterdex 全历史上净正？
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.replay import replay_symbol, _load_series  # noqa: E402

OUT = ROOT / "data" / "mm_param_sweep_v12.json"
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
EQUITY = 5000.0
FILL_NOTIONAL = 100.0

GRID = {
    "w_base_bp": (5.0, 8.0),
    "k_inv": (0.6, 1.0),
    "max_one_side_sec": (120.0, 300.0, 900.0),
    "stop_loss_bp": (0.0, 10.0, 20.0),
    "trend_pause_bp": (0.0, 5.0, 10.0),
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
                    for tr in GRID["trend_pause_bp"]:
                        params = QuoteParams(w_base_bp=w, k_inv=k)
                        limits = LaneRiskLimits(
                            max_one_side_seconds=hold,
                            stop_loss_bp=sl,
                            trend_pause_bp=tr,
                            trend_lookback=20,
                            vol_pause_mult=1.5,
                            vol_pause_sigma=1.5,
                            max_net_directional_ratio=0.04,
                            max_net_exposure_ratio=0.06,
                        )
                        agg_notional = 0.0
                        agg_net = 0.0
                        agg_fills = 0
                        agg_flat = 0
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
                            }
                            agg_notional += res.notional
                            agg_net += res.net_usd
                            agg_fills += res.fills
                            agg_flat += res.flattens
                        net_bp = agg_net / agg_notional * 1e4 if agg_notional > 0 else 0.0
                        rows.append({
                            "w_base_bp": w, "k_inv": k, "max_one_side_sec": hold,
                            "stop_loss_bp": sl, "trend_pause_bp": tr,
                            "fills": agg_fills, "flattens": agg_flat,
                            "flatten_share": round(agg_flat / agg_fills, 3) if agg_fills else None,
                            "net_bp": round(net_bp, 3),
                            "net_usd": round(agg_net, 4),
                            "per_symbol": per,
                        })
                        print(f"w={w} k={k} hold={hold}s sl={sl} trend={tr} -> "
                              f"fills={agg_fills} flat={agg_flat} net={net_bp:+.3f}bp ({agg_net:+.2f} USD)")

    rows_sorted = sorted(rows, key=lambda r: -r["net_bp"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "equity": EQUITY, "fill_notional": FILL_NOTIONAL,
        "symbols": SYMBOLS,
        "rows": rows,
        "top10": rows_sorted[:10],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== TOP 10 ===")
    for r in rows_sorted[:10]:
        print(r)
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
