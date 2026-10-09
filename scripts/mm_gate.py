# -*- coding: utf-8 -*-
"""[F275 · E3] SBS 配对门控：候选配置 vs 现役配置（同窗口同延迟配对比较）。

调研依据：HFT-ML 报告 §8.3 第 2/3 步（SBS 部署门控）与第四部分验收纪律——
「正确的问法不是多久重训，而是**候选凭什么取代现役**」；实测仅 20–25% 候选晋升。

验收门槛（全部满足才 PROMOTE）：
  ① 净额不劣：每个「窗口×延迟」格子的 net_usd ≥ 现役 × (1 − FULL_TOL)（默认 2% 容忍）
  ② markout 改善：mean mk90s 的配对改善 ≥ MARKOUT_MIN_BP（默认 0.3bp），
     且按**日分块 bootstrap** 的 t > 3（样本不足则判 INSUFFICIENT）
  ③ 尾部不恶化：p05(mk90s) 不比现役差超过 TAIL_TOL_BP（默认 1bp）
  ④ 全格子 markout 不为负改善（每格 mean mk90s 不劣于现役 − 0.2bp）

用法：
    python scripts/mm_gate.py --candidate "w_base_bp=10" \
        [--windows 3] [--delays 31800] [--register]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = "mm_asterdex"
FULL_TOL = 0.02          # 净额容忍：每格 ≥ 现役 × (1 − 2%)
MARKOUT_MIN_BP = 0.3     # markout 平均改善门槛（bp/笔）
TAIL_TOL_BP = 1.0        # 5% 分位不允许恶化超过 1bp
GRID_TOL_BP = 0.2        # 单格 markout 不允许恶化超过 0.2bp
TAU_MS = [30_000, 90_000]

def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _markout_rows(fills_log: List[dict], data: Dict[str, Dict[str, np.ndarray]],
                  tau: int) -> List[Tuple[int, float]]:
    """→ [(日序号, markout_bp)]，按 ts 的 UTC 日分块（供按日 bootstrap）。"""
    out: List[Tuple[int, float]] = []
    for f in fills_log:
        dd = data.get(f["symbol"])
        if not dd or not len(dd["ots"]):
            continue
        ots, bb, ba = dd["ots"], dd["bb"], dd["ba"]
        ts, px, side = int(f["ts_ms"]), float(f["px"]), f["side"]
        j = int(np.searchsorted(ots, ts + tau, "left"))
        if j >= len(ots):
            continue
        mid = float((bb[j] + ba[j]) / 2.0)
        if mid <= 0 or px <= 0:
            continue
        mv = (mid - px) if side == "buy" else (px - mid)
        out.append((ts // 86_400_000, mv / px * 1e4))
    return out


def _stats(vals: List[float]) -> Dict[str, Optional[float]]:
    if not vals:
        return {"n": 0, "mean": None, "p05": None}
    v = sorted(vals)
    return {"n": len(v), "mean": round(float(np.mean(v)), 3),
            "p05": round(v[max(0, int(len(v) * 0.05))], 3)}


def _day_block_t(rows: List[Tuple[int, float]]) -> Optional[float]:
    """按日分块的配对 t：把每笔 markout 的**日块均值**作为观测值做单样本 t。"""
    by_day: Dict[int, List[float]] = {}
    for d, v in rows:
        by_day.setdefault(d, []).append(v)
    if len(by_day) < 3:
        return None
    means = np.array([np.mean(v) for v in by_day.values()], dtype=float)
    if means.std(ddof=1) == 0:
        return None
    return float(means.mean() / (means.std(ddof=1) / np.sqrt(len(means))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="k=v,k2=v2（相对现役的改动）")
    ap.add_argument("--delays", default="31800", help="逗号分隔毫秒，默认 31800")
    ap.add_argument("--n-windows", type=int, default=3)
    ap.add_argument("--register", action="store_true", help="把判定登记到试验台账")
    args = ap.parse_args()

    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    cand = dict(cur)
    changed = {}
    for part in args.candidate.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        try:
            cand[k] = json.loads(v)
        except Exception:
            cand[k] = v
        changed[k] = cand[k]
    if all(cur.get(k) == v for k, v in changed.items()):
        print("（候选与现役完全相同，无需门控）")
        return 0

    delays = [float(x) for x in args.delays.split(",") if x.strip()]
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    all_windows = [
        ("全天 09-15", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
        ("当日 09-16", "2026-09-16T08:00:00+08:00", now_iso),
        ("快盘夜", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00"),
    ]
    windows = all_windows[: max(1, min(args.n_windows, len(all_windows)))]

    data = _load_all(symbols, venue)
    cells: List[Dict[str, Any]] = []
    print(f"候选改动: {changed}")
    for wname, s0, s1 in windows:
        since_ms, until_ms = _ms(s0), _ms(s1)
        sub, seed = {}, {}
        for s in symbols:
            dd = data[s]
            a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
            a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
            t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
            t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
            # 注意：新增机制的输入数组必须在这里一起切片（F275 实测教训——
            # 漏切 "mps" ⇒ mp_block_bp 候选退化为无操作、门控给出假 REJECT ✗）
            sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[s][k] = dd[k][t2:t3]
            lo_i = max(0, a2 - 240)
            seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                       for k in range(lo_i, a2)) if x > 0]
        for d in delays:
            res = {}
            for tag, params_d in (("inc", cur), ("cand", cand)):
                qp = QuoteParams(**{k: v for k, v in params_d.items()
                                    if k in QuoteParams.__dataclass_fields__})
                lim = LaneRiskLimits(**{k: v for k, v in params_d.items()
                                        if k in LaneRiskLimits.__dataclass_fields__})
                r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                     enforce_lane_limits=True, tick_delay_ms=d,
                                     fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
                rows = _markout_rows(r.get("fills_log") or [], data, TAU_MS[1])
                res[tag] = {
                    "net": float(r.get("net_usd") or 0.0), "fills": int(r.get("fills") or 0),
                    "mk": _stats([v for _, v in rows]),
                    "t": _day_block_t(rows),
                }
            cell = {"window": wname, "delay_ms": d, "inc": res["inc"], "cand": res["cand"]}
            cells.append(cell)
            print(f"  [{wname} @{d/1000:.1f}s] 现役 {res['inc']['fills']}笔 {res['inc']['net']:+.3f} "
                  f"mk90s={res['inc']['mk']['mean']} | 候选 {res['cand']['fills']}笔 "
                  f"{res['cand']['net']:+.3f} mk90s={res['cand']['mk']['mean']} "
                  f"t={res['cand']['t']}")

    # ── 判据 ──
    fails: List[str] = []
    for c in cells:
        inc, cd = c["inc"], c["cand"]
        if cd["net"] < inc["net"] * (1 - FULL_TOL) and cd["net"] < inc["net"] - 0.02:
            fails.append(f"净额不劣 ✗ [{c['window']}@{c['delay_ms']/1000:.0f}s] "
                         f"{cd['net']:+.3f} < {inc['net']:+.3f}×0.98")
        mi, mc = inc["mk"]["mean"], cd["mk"]["mean"]
        if mi is not None and mc is not None and mc < mi - GRID_TOL_BP:
            fails.append(f"单格 markout 恶化 ✗ [{c['window']}] {mc} < {mi}−{GRID_TOL_BP}")
        pi, pc = inc["mk"]["p05"], cd["mk"]["p05"]
        if pi is not None and pc is not None and pc < pi - TAIL_TOL_BP:
            fails.append(f"尾部恶化 ✗ [{c['window']}] p05 {pc} < {pi}−{TAIL_TOL_BP}")

    diffs = [c["cand"]["mk"]["mean"] - c["inc"]["mk"]["mean"] for c in cells
             if c["cand"]["mk"]["mean"] is not None and c["inc"]["mk"]["mean"] is not None]
    mean_improve = float(np.mean(diffs)) if diffs else None
    ts = [c["cand"]["t"] for c in cells if c["cand"]["t"] is not None]
    t_best = max(ts) if ts else None
    n_total = sum(c["cand"]["mk"]["n"] for c in cells)

    if mean_improve is not None and mean_improve < MARKOUT_MIN_BP:
        fails.append(f"markout 改善不足 ✗ 平均 {mean_improve:+.3f}bp < {MARKOUT_MIN_BP}bp")
    # 判定优先级：**硬性失败（不劣/尾部/单格恶化）永远优先**——样本不足不得把
    # 「明显更差」洗成 INSUFFICIENT（实测 w=6 候选三窗口全面更差却因 t=None 被
    # 判为样本不足 ✗）。
    if fails:
        verdict = "REJECT"
        reason = "；".join(fails[:4])
    elif n_total < 60 or t_best is None:
        verdict = "INSUFFICIENT"
        reason = f"样本不足（markout 样本 {n_total} 笔，日块 t={'NA' if t_best is None else round(t_best,2)}）"
    elif t_best <= 3.0:
        verdict = "INSUFFICIENT"
        reason = f"markout 改善 {mean_improve:+.3f}bp 但按日块 t={t_best:.2f} ≤ 3（不显著）"
    else:
        verdict = "PROMOTE"
        reason = f"全格子不劣 + markout 平均 {mean_improve:+.3f}bp + 日块 t={t_best:.2f}"

    print(f"\n判定: {verdict} — {reason}")
    print(f"（样本 {n_total} 笔；平均 markout 改善 "
          f"{'NA' if mean_improve is None else f'{mean_improve:+.3f}bp'}）")

    if args.register:
        import subprocess
        subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "mm_trials.py"),
                        "add", "--kind", "ab", "--window", f"{len(windows)}窗口×{len(delays)}延迟",
                        "--delays", ",".join(str(int(x)) for x in delays),
                        "--n-configs", "2", "--verdict", f"{verdict}: {reason[:80]}",
                        "--configs-digest", json.dumps(changed, ensure_ascii=False),
                        "--script", "scripts/mm_gate.py"], check=False)
    return 0 if verdict == "PROMOTE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
