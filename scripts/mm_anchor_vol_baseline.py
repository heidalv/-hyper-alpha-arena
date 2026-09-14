# -*- coding: utf-8 -*-
"""[F96] 锚定「已实现波动基准」到车道注册表（实盘与回放共用同一口径）。

为什么必须锚定：`k_vol>0` 时挂宽 = w_base × (1 + k_vol × σ)，σ = 当前已实现波动 /
**基准**。实盘只对注册表 `meta.replay_baseline.vol_baseline_bp` 里**存在**的币覆写，
其余沿用持久化的陈旧值 —— 实测 ETH/BNB/XRP/SOL 的实盘基准比回放低 15~21%，
σ 因此系统性偏高 ⇒ **实盘挂的单比回放宽**（成交更少、bp 更高），回放结论无法复现。

用法:
    python scripts/mm_anchor_vol_baseline.py                 # 打印并写入注册表
    python scripts/mm_anchor_vol_baseline.py --dry-run       # 只看差异
    python scripts/mm_anchor_vol_baseline.py --days 14
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.portfolio_replay import (  # noqa: E402
    _load_all,
    compute_vol_baselines,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--days", type=float, default=14.0, help="回放窗口（天）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lane = reg.get_lane(args.lane)
    if not lane:
        print(f"车道不存在: {args.lane}")
        return 1
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    if not symbols:
        print("车道未配置 symbols")
        return 1

    data = _load_all(symbols, venue)
    mid_series = {}
    for s in symbols:
        d = data[s]
        ms = [float((d["bb"][i] + d["ba"][i]) / 2) for i in range(len(d["bb"]))]
        mid_series[s] = [m for m in ms if m > 0]
    new = compute_vol_baselines(mid_series)
    old = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    print(f"车道 {args.lane}  币种 {symbols}  窗口 {args.days} 天")
    print(f"{'币':<5}{'注册表现值':>12}{'重算基准':>12}{'变化':>10}")
    changed = []
    for s in symbols:
        o, n = float(old.get(s) or 0.0), float(new.get(s) or 0.0)
        pct = ((n / o - 1) * 100) if o > 0 else float("inf")
        print(f"{s:<5}{o:>12.4f}{n:>12.4f}"
              f"{('  新锚定' if o <= 0 else f'{pct:>+9.1f}%'):>10}")
        if abs(n - o) > 1e-6:
            changed.append(s)

    if not changed:
        print("\n✅ 注册表基准与重算一致，无需更新")
        return 0
    if args.dry_run:
        print(f"\n（dry-run）需更新 {len(changed)} 个币: {changed}")
        return 0
    rb = dict(meta.get("replay_baseline") or {})
    rb["vol_baseline_bp"] = {s: round(float(new.get(s) or 0.0), 6) for s in symbols}
    rb["vol_baseline_anchored_at"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()
    rb["vol_baseline_source"] = f"compute_vol_baselines({args.days}d, median realized_vol_bp)"
    meta["replay_baseline"] = rb
    ok = reg.update_meta(args.lane, meta)
    print(f"\n{'✅ 已写入注册表' if ok else '❌ 写入失败'}: {len(changed)} 个币更新")
    print("（实盘 runner 在下次重建时读取，需重启后端生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
