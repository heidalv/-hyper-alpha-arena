# -*- coding: utf-8 -*-
"""H432 v2 出场架构影子测验：A=现行(T1-T5) vs B=+exit_skew/vol_spread，近 12h 回放。

用官方回放器（replay_symbol，真实盘口+真实费率），只比 compute_quote 参数差异。
输出两臂的净额/强平/分折对比，判定 B 是否不劣于 A。
用法: python scripts/h432_v2_shadow.py [--hours 12]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h432_v2_shadow.json"


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    from backend.services.market_maker.core import QuoteParams, LaneRiskLimits
    from backend.services.market_maker.replay import replay_symbol, _load_series

    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
    syms = [str(s) for s in (m.get("symbols") or []) if str(s)]
    params_live = dict(m.get("params") or {})

    start_ts = int((dt.datetime.now(dt.timezone.utc)
                    - dt.timedelta(hours=a.hours)).timestamp() * 1000)

    def mk_params(**over):
        p = {k: v for k, v in params_live.items()
             if k in QuoteParams.__dataclass_fields__}
        p.update(over)
        return QuoteParams(**p)

    def mk_limits():
        return LaneRiskLimits(
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
            max_net_exposure_ratio=float(params_live.get("max_net_exposure_ratio") or 24.0),
            max_gross_notional_ratio=float(params_live.get("max_gross_notional_ratio") or 48.0),
        )

    pa = mk_params()
    pb = mk_params(vol_spread_k=0.5, exit_skew_k=1.0, exit_skew_scale_bp=40.0)
    lim = mk_limits()

    arms = {"A_现状": pa, "B_v2": pb}
    res = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "hours": a.hours, "symbols": syms, "arms": {}}

    for name, params in arms.items():
        tot = {"fills": 0, "notional": 0.0, "net_usd": 0.0, "spread_usd": 0.0,
               "fee_usd": 0.0, "price_usd": 0.0, "flattens": 0}
        per = {}
        for sym in syms:
            try:
                ser = _load_series(sym, "asterdex", start_ts)
                r = replay_symbol(sym, venue="asterdex", equity=300.0,
                                  start_ts=start_ts, params=params, limits=lim,
                                  series=ser)
            except Exception as e:
                print(f"  {sym}: 回放失败 {e}", flush=True)
                continue
            per[sym] = r.to_dict()
            for k in ("fills", "notional", "net_usd", "spread_usd", "fee_usd",
                      "price_usd", "flattens"):
                tot[k] += float(getattr(r, k) or 0.0)
        tot["net_bp"] = round(tot["net_usd"] / max(1e-9, tot["notional"]) * 1e4, 3)
        tot["spread_bp"] = round(tot["spread_usd"] / max(1e-9, tot["notional"]) * 1e4, 3)
        tot["fee_bp"] = round(tot["fee_usd"] / max(1e-9, tot["notional"]) * 1e4, 3)
        tot["price_bp"] = round(tot["price_usd"] / max(1e-9, tot["notional"]) * 1e4, 3)
        res["arms"][name] = {"totals": tot, "per_symbol": per}
        print(f"{name}: fills={tot['fills']} notional={tot['notional']:.0f} "
              f"net={tot['net_bp']}bp spread={tot['spread_bp']}bp "
              f"price={tot['price_bp']}bp fee={tot['fee_bp']}bp "
              f"flattens={tot['flattens']}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT}")

    a_t = res["arms"]["A_现状"]["totals"]
    b_t = res["arms"]["B_v2"]["totals"]
    verdict = "B 不劣于 A（影子通过）" if b_t["net_bp"] >= a_t["net_bp"] else "B 劣于 A（影子不通过）"
    print(f"影子裁决：{verdict}（A {a_t['net_bp']}bp vs B {b_t['net_bp']}bp）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
