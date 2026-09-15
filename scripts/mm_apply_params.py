"""按"可审计"的方式改车道参数（复用自进化自己的落地路径）。

[F189 2026-09-15] 为什么不让脚本直接写 DB：
`evolution._apply_params` 已经把「新参数 + prev_params（供回滚）+ 原因 + 模式」写进
注册表 meta ✓，自进化的自动回滚也读这份 prev_params ✓。人工改动若走别的路径，
自动回滚就**不会**认识这次变更（回滚到错误的基线）✗。所以人工变更也必须走同一个入口。

用法：
    python scripts/mm_apply_params.py --set w_base_bp=12.0,min_width_reduce_bp=6.0 \
        --reason "F189 干净数据 3 口径扫描：..." [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker import evolution as evo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--set", required=True, help="k=v,k2=v2")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lane = reg.get_lane(args.lane)
    if not lane:
        print(f"✗ 车道不存在: {args.lane}")
        return 2
    meta = dict(lane.get("meta") or {})
    cur = dict(meta.get("params") or {})

    new: dict = {}
    for part in args.set.split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        try:
            new[k] = json.loads(v)
        except Exception:
            new[k] = v

    prev = {k: cur.get(k) for k in new}
    print(f"车道 {args.lane}")
    for k, v in new.items():
        print(f"  {k}: {cur.get(k)!r} → {v!r}")
    noop = all(cur.get(k) == v for k, v in new.items())
    if noop:
        print("（无变化，未写入）")
        return 0
    if args.dry_run:
        print("--dry-run：未写入 ✓")
        return 0

    ok = evo._apply_params(args.lane, meta, new, prev=prev, reason=args.reason)
    print(f"写入结果: {ok}")
    after = dict((reg.get_lane(args.lane) or {}).get("meta") or {}).get("params") or {}
    print("复核（注册表现值）: " + " ".join(f"{k}={after.get(k)!r}" for k in new))
    ev = dict((reg.get_lane(args.lane) or {}).get("meta") or {}).get("evolution") or {}
    print(f"evolution.prev_params = {ev.get('prev_params')}")
    print(f"evolution.reason      = {ev.get('reason')}")
    print("⚠ 需要重启后端 / 车道进程才会生效（get_runner 在启动时读注册表）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
