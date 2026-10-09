# -*- coding: utf-8 -*-
"""[h650/D1 2026-09-30] D1 趋势对齐闸试跑判决脚本(预注册口径,与协议文档配套)。

协议:`研究结论/d1_trend_gate_trial_protocol_20260930.md`
[h651] 全部归因逻辑已迁移至 `backend/services.market_maker.attribution`
(共享事实源,《方向判定…设计》§9.1);本文件只剩 CLI 与判决裁决。

用法:
    python scripts/d1_verdict.py --trial-start "2026-09-30T03:45:00Z" \
        --baseline-hours 12 --out research_l1/out/d1_trend_gate_verdict.json
    python scripts/d1_verdict.py --preview   # 用最近 baseline-hours 当基线预览(不裁决)
只读,不写任何表。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.attribution import (  # noqa: E402
    LOOKBACK_SEC, bucket_side, fetch_legs, fetch_mid_series,
    pair_roundtrips_chrono, trend_bp_at, welch_two_sided,
)


def run(trial_start_iso: Optional[str], baseline_hours: float,
        lane_id: str, trial_end_iso: Optional[str] = None) -> Dict[str, Any]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).timestamp()
    if trial_start_iso:
        trial_start = datetime.fromisoformat(
            trial_start_iso.replace("Z", "+00:00")).timestamp()
        # [h652 提前裁决] trial_end 用于"预注册地提前切窗":只统计
        # open_ts < trial_end 的往返,窗口 = [start, end]。不传 = 窗口开到 now。
        trial_end = (datetime.fromisoformat(trial_end_iso.replace("Z", "+00:00")).timestamp()
                     if trial_end_iso else now)
    else:
        trial_start = now  # 预览模式:全窗当基线
        trial_end = now
    since = trial_start - baseline_hours * 3600.0
    legs = fetch_legs(since, lane_id)
    trips = pair_roundtrips_chrono(legs, since_epoch=since)
    # [h664 审计#5 修复] 判决 A 用**腿数**(fills/小时)而非往返数:趋势闸压的是
    # 加仓腿,往返数对腿量塌缩结构性失明;同时补协议"≥60 腿/h"硬指标。
    n_legs_base = sum(1 for lg in legs if lg["ts_epoch"] < trial_start)
    n_legs_trial = (sum(1 for lg in legs
                        if trial_start <= lg["ts_epoch"] < trial_end)
                    if trial_start_iso else 0)
    if trial_start_iso:
        base_trips = [t for t in trips if t["open_ts"] < trial_start]
        trial_trips = [t for t in trips
                       if trial_start <= t["open_ts"] < trial_end]
        trial_hours = max((trial_end - trial_start) / 3600.0, 0.001)
        trial_rate = len(trial_trips) / trial_hours
        trial_legs_rate = n_legs_trial / trial_hours
        base_hours = baseline_hours
        base_rate = len(base_trips) / base_hours
        base_legs_rate = n_legs_base / base_hours
    else:
        base_trips = trips
        trial_trips = []
        trial_rate = 0.0
        trial_legs_rate = 0.0
        base_rate = len(base_trips) / baseline_hours
        base_legs_rate = n_legs_base / baseline_hours
        trial_hours = 0.0

    # 中价回填(仅对需要 bucket 的往返,按币拉一次序列)
    syms = {t["symbol"] for t in trips}
    mid_series: Dict[str, Tuple[List[float], List[float]]] = {}
    t_min = min((t["quote_ts"] for t in trips if t["quote_ts"] > 0),
                default=since) - LOOKBACK_SEC - 60
    for sym in syms:
        try:
            # [h651 修复] attribution.fetch_mid_series 内部自己加 USDT 后缀,
            # 这里必须传**裸币名**,否则双重重命名(BTCUSDTUSDT → 0 行 → 全 unknown)。
            mid_series[sym] = fetch_mid_series(sym, t_min, now)
        except Exception:
            mid_series[sym] = ([], [])

    def tag(trip: Dict[str, Any]) -> Dict[str, Any]:
        t = dict(trip)
        series = mid_series.get(trip["symbol"], ([], []))
        tb = trend_bp_at(series[0], series[1], t["quote_ts"])
        t["trend_bp"] = tb
        t["bucket"] = bucket_side(t["side"], tb)
        return t

    base_tagged = [tag(t) for t in base_trips]
    trial_tagged = [tag(t) for t in trial_trips]

    def agg(ts: List[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"n": len(ts), "all": {}, "with": {}, "against": {},
                               "unknown": {}}
        for b in ("all", "with", "against", "unknown"):
            sub = ts if b == "all" else [t for t in ts if t["bucket"] == b]
            if not sub:
                out[b] = {"n": 0, "net_bp": None}
                continue
            w = sum(t["notional"] for t in sub) or 1.0
            out[b] = {"n": len(sub),
                      "net_bp": sum(t["net_bp"] * t["notional"]
                                    for t in sub) / w}
        return out

    base_agg = agg(base_tagged)
    trial_agg = agg(trial_tagged)

    verdict = "NO_TRIAL"
    stats: Dict[str, Any] = {}
    if trial_start_iso:
        n_all = int(trial_agg["all"]["n"] or 0)
        n_ag = int(trial_agg["against"]["n"] or 0)
        if n_all == 0:
            verdict = "NO_DATA"
        elif n_all < 15:
            verdict = "NO_DATA"          # 协议:每组 <15 笔只报样本不足,不算通过/失败
        elif trial_legs_rate < 0.8 * base_legs_rate:
            verdict = "ROLLBACK_A_RATE"  # [h664 修复] 用腿数/fills,非往返数
        else:
            w_all = welch_two_sided(
                [t["net_bp"] for t in trial_tagged],
                [t["net_bp"] for t in base_tagged])
            w_ag = welch_two_sided(
                [t["net_bp"] for t in trial_tagged if t["bucket"] == "against"],
                [t["net_bp"] for t in base_tagged if t["bucket"] == "against"])
            stats = {"welch_all": w_all, "welch_against": w_ag,
                     "against_min_n_met": n_ag >= 15}
            p_all = w_all.get("p")
            p_ag = w_ag.get("p")
            d_all = (trial_agg["all"]["net_bp"] or 0) - (base_agg["all"]["net_bp"] or 0)
            if p_all is not None and p_all < 0.10 and d_all < 0:
                verdict = "ROLLBACK_B_WORSE"
            elif (n_ag >= 15 and p_ag is not None and p_ag < 0.10
                  and (trial_agg["against"]["net_bp"] or 0)
                  > (base_agg["against"]["net_bp"] or 0)):
                verdict = "KEEP_D_AGAINST_BETTER"
            elif p_all is not None and p_all < 0.10 and d_all > 0:
                verdict = "KEEP_D_ALL_BETTER"
            else:
                verdict = "EXTEND_C"

    result = {
        "generated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "lane_id": lane_id, "trial_start": trial_start_iso,
        "trial_end": (trial_end_iso
                      or (datetime.fromtimestamp(trial_end, tz=timezone.utc).isoformat()
                          if trial_start_iso else None)),
        "baseline_hours": baseline_hours,
        "baseline": {"rate_per_h": round(base_rate, 2),
                     "legs_per_h": round(base_legs_rate, 2), **base_agg},
        "trial": {"rate_per_h": round(trial_rate, 2),
                  "legs_per_h": round(trial_legs_rate, 2),
                  "legs_per_h_ok": bool(trial_legs_rate >= 60.0),
                  "hours": round(trial_hours, 2), **trial_agg},
        "verdict": verdict, "stats": stats,
        # [h651 回归教训] unknown 哨兵:趋势回填坏了会把全部往返推进 unknown
        # (15:10 实测:双重重命名 → 100% unknown → 分桶裁决全部失效)。
        # 占比 > 20% 时必须先修数据回填,再谈裁决。
        "unknown_share_warn": bool(
            (trial_agg["unknown"].get("n") or 0) >
            0.2 * max(int(trial_agg["all"].get("n") or 0), 1)),
        "note": ("阈值:腿数<0.8×基线⇒ROLLBACK_A;总体bp显著更差⇒ROLLBACK_B;"
                 "逆势桶/总体显著改善⇒KEEP;否则EXTEND。α=0.10双侧Welch(H372口径)。"
                 "逆势桶 n<15 只报样本不足。" if trial_start_iso else "预览模式:未裁决"),
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial-start", default=None,
                    help="ISO 时间(UTC),如 2026-09-30T03:45:00Z;缺省=预览模式")
    ap.add_argument("--preview", action="store_true",
                    help="显式预览模式(近 baseline-hours 全当基线,不裁决)")
    ap.add_argument("--baseline-hours", type=float, default=12.0)
    ap.add_argument("--trial-end", default=None,
                    help="ISO 时间(UTC):预注册提前切窗,只统计 open_ts < 该时刻的往返")
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--out", default="research_l1/out/d1_trend_gate_verdict.json")
    args = ap.parse_args()
    res = run(args.trial_start, args.baseline_hours, args.lane, args.trial_end)
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
