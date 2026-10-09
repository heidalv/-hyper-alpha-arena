# -*- coding: utf-8 -*-
"""[F239 2026-09-16] 每日体检摘要：把"多日确认"变成一条命令 + 一份可累积的记录。

每个自然日跑一次：回放**上一个完整自然日**（08:00→23:59，+08:00）用**在位配置**
三口径，同时抓实盘行为做对比（F196：行为先核对、再谈收益），把一行 JSON 追加到
`logs/mm_daily_digest.jsonl`。之后的每个新自然日重复执行即自动积累干净数据证据 ✓。

用法：python scripts/mm_daily_digest.py [--day 2026-09-15]   # 默认=昨天
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker import evolution as evo  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import (  # noqa: E402
    _load_all,
    replay_portfolio,
)

LANE = "mm_asterdex"
OUT = r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_daily_digest.jsonl"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="", help="YYYY-MM-DD（+08:00 自然日）；默认=昨天")
    args = ap.parse_args()

    if args.day:
        day = args.day
    else:
        day = (datetime.now(timezone.utc).astimezone() - timedelta(days=1)).strftime("%Y-%m-%d")
    since_txt = f"{day}T08:00:00+08:00"
    until_txt = f"{day}T23:59:00+08:00"

    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    delays = list(evo.ROBUST_DELAYS_MS)

    data = _load_all(symbols, venue)
    since_ms, until_ms = _ms(since_txt), _ms(until_txt)
    sub, seed = {}, {}
    for s in symbols:
        dd = data[s]
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
        t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
        t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
        sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba")}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = dd[k][t2:t3]
        lo_i = max(0, a2 - 240)
        seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                   for k in range(lo_i, a2)) if x > 0]

    model = []
    for d in delays:
        r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                             fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                             enforce_lane_limits=True, tick_delay_ms=float(d),
                             fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
        model.append({"delay_ms": d, "fills": r.get("fills"), "net_usd": r.get("net_usd"),
                      "net_bp": r.get("net_bp"), "dd_pct": r.get("max_dd_pct"),
                      "maker_bp": r.get("maker_net_bp"), "flatten_bp": r.get("flatten_net_bp"),
                      "flattens": r.get("flattens"), "max_gross_usd": r.get("max_gross_usd")})
        print(f"[{day}] 模型 {d/1000:>4.1f}s  fills={r.get('fills'):>4}"
              f"  net={r.get('net_usd'):>+8.3f}$ ({r.get('net_bp'):>+7.3f}bp)"
              f"  dd={r.get('max_dd_pct')}%  maker={r.get('maker_net_bp')}bp"
              f"  flatten={r.get('flatten_net_bp')}bp x{r.get('flattens')}"
              f"  gross峰值={r.get('max_gross_usd')}")

    live = {}
    try:
        import urllib.request
        sh = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:8000/api/trading/lanes/{LANE}/shadow", timeout=30).read())
        ua = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8000/api/trading/account/unified", timeout=30).read())
        live = {
            "fills_per_hour": sh.get("fills_per_hour"),
            "avg_width_bp": sh.get("avg_width_bp"),
            "avg_sigma_all": sh.get("avg_sigma_all"),
            "skip_counts": sh.get("skip_counts"),
            "positions": {k: v.get("qty") for k, v in (sh.get("states") or {}).items()
                          if abs(v.get("qty") or 0) > 1e-9},
            "equity": ua["account"]["equity"],
            "era_realized": ua["account"]["realized_pnl"],
            "era_fills": ua["era"]["fills"],
        }
        print(f"[{day}] 实盘 fills/h={live['fills_per_hour']} 宽={live['avg_width_bp']}"
              f" σ_all={live['avg_sigma_all']} 仓={live['positions']}"
              f" eq={live['equity']} era_realized={live['era_realized']}"
              f" era_fills={live['era_fills']}")
    except Exception as e:
        print(f"[{day}] 实盘抓取失败: {e}")

    # [F287 2026-09-16] 链路连续性（F286 度量）纳入每日结论：车道 tick 覆盖率 / 断点分钟。
    # 为什么必须进 digest：此前"链路断没断"只能靠重启日志人工推断，而进程在跑 ≠
    # 车道在 tick（F283 后 ticker 是独立 worker）⇒ 每天的结论要自带这一行证据 ✓。
    link = {}
    try:
        import subprocess as _sp
        _r = _sp.run([sys.executable, str(Path(__file__).resolve().parent / "mm_link_coverage.py"),
                      "--day", day], capture_output=True, text=True, timeout=90,
                     encoding="utf-8", errors="replace")
        _out = (_r.stdout or "").strip().splitlines()
        for _ln in _out:
            print(_ln)
        for _ln in _out:
            if "覆盖率" in _ln:
                link["coverage_line"] = _ln.strip()
        link["raw"] = _out
    except Exception as e:
        print(f"[{day}] 链路连续性度量失败: {e}")
        link["error"] = str(e)

    # [F300 2026-09-16] 按**宇宙时代**分段统计：宇宙在当天变过（F296：13:55 由 BTC,ETH
    # 切到 LINK）⇒ 把全天混算会把两套完全不同的微观结构样本加在一起（旧段 ETH 只能赚
    # 0.04bp 的锁死盘口，新段 LINK 对手盘价差 2.8bp）。分段口径写死在下面，随宇宙变更
    # 追加一行即可；实盘/模型对拍也必须按时代做（F196）。
    eras: Dict[str, Any] = {}
    try:
        from sqlalchemy import text as _text

        from backend.core.tenant import system_identity as _si
        from backend.database.connection import SessionLocal as _SL

        for _name, _a, _b in (("btc_eth", "2026-09-16T11:05:17+08:00", "2026-09-16T13:55:00+08:00"),
                              ("link", "2026-09-16T13:55:00+08:00", None)):
            _t0 = datetime.fromisoformat(_a)
            _t1 = datetime.fromisoformat(_b) if _b else datetime.now(timezone.utc).astimezone()
            with _si(), _SL() as _db:
                _row = _db.execute(_text(
                    "SELECT COUNT(*) n, COALESCE(SUM(notional),0) notional,"
                    " COALESCE(SUM(points_usd),0) pts FROM lane_ledger"
                    " WHERE lane_id='mm_asterdex' AND ts >= :a AND ts < :b"
                    "   AND COALESCE((meta_json->>'source'),'') <> 'reconcile'"),
                    {"a": _t0, "b": _t1}).mappings().first()
            eras[_name] = {"since": _a, "until": _b or "now",
                           "fills": int(_row["n"] or 0),
                           "notional": round(float(_row["notional"] or 0), 2),
                           "realized_usd": round(float(_row["pts"] or 0), 4)}
            print(f"[{day}] 时代 {_name}（{_a[11:16]}~{(_b[11:16] if _b else 'now')}）: "
                  f"fills={eras[_name]['fills']} notional={eras[_name]['notional']}$ "
                  f"realized={eras[_name]['realized_usd']:+}$")
    except Exception as e:
        print(f"[{day}] 时代分段统计失败: {e}")

    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
           "day": day, "params": {k: cur.get(k) for k in sorted(cur)
                                  if k in QuoteParams.__dataclass_fields__
                                  or k in LaneRiskLimits.__dataclass_fields__},
           "model": model, "live": live, "link_continuity": link, "eras": eras}
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[{day}] 已追加 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
