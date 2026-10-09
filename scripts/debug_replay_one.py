# -*- coding: utf-8 -*-
"""单币回放调试：BTC 一臂，打印 skipped/quote_modes/fills 全貌。只读。"""
import sys
import json
import pathlib
import datetime as dt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from backend.services.market_maker.core import QuoteParams, LaneRiskLimits
from backend.services.market_maker.replay import replay_symbol, _load_series

with psycopg.connect(read_env_dsn(), autocommit=True) as c:
    with c.cursor() as cur:
        cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
        m = cur.fetchone()[0]
params_live = dict(m.get("params") or {})

start_ts = int((dt.datetime.now(dt.timezone.utc)
                - dt.timedelta(hours=12)).timestamp() * 1000)
ser = _load_series("BTC", "asterdex", start_ts)
print("series lens:", [len(x) for x in ser[:8]])

params = QuoteParams(**{k: v for k, v in params_live.items()
                        if k in QuoteParams.__dataclass_fields__})
lim = LaneRiskLimits(
    stop_loss_bp=float(params_live.get("stop_loss_bp") or 0.0),
    max_one_side_seconds=float(params_live.get("max_one_side_seconds") or 0.0),
    stop_maker_grace_sec=float(params_live.get("stop_maker_grace_sec") or 0.0),
    jump_exit_bp=float(params_live.get("jump_exit_bp") or 0.0),
    timeout_hard_taker_sec=float(params_live.get("timeout_hard_taker_sec") or 0.0),
    stop_loss_vol_min=float(params_live.get("stop_loss_vol_min") or 0.0),
    sudden_move_cooldown_sec=float(params_live.get("sudden_move_cooldown_sec") or 0.0),
    trend_pause_bp=float(params_live.get("trend_pause_bp") or 0.0),
    max_net_directional_ratio=float(params_live.get("max_net_directional_ratio") or 2.0),
    max_symbol_notional_ratio=float(params_live.get("max_symbol_notional_ratio") or 12.0),
)
r = replay_symbol("BTC", venue="asterdex", equity=300.0, start_ts=start_ts,
                  params=params, limits=lim, series=ser)
d = r.to_dict()
for k in ("snapshots", "window_days", "covered_days", "fills", "notional",
          "net_usd", "flattens", "quoted_decisions", "skipped", "quote_modes",
          "vol_baseline_bp", "spread_usd", "price_usd", "fee_usd"):
    print(f"{k}: {d.get(k)}")
