# -*- coding: utf-8 -*-
"""[F267] 车道币种宇宙变更（审计路径）：meta.symbols = BTC+ETH。

依据 F266 决策档案：BTC+ETH vs 全 5 币 9/9 不劣、8/9 严格更优；三窗口合计
31.8s +$0.109 vs −$0.773；回撤 0.04~0.09% vs 0.15~0.31%（用户批准）。
副作用处理：被移出宇宙的币若仍有持仓，runner 的 F90 孤儿逻辑会在下一 tick
用对手价平掉（当前三币均空仓，无遗留）；lane_runtime_state 旧行由 prune 清理。
用法：python scripts/mm_set_symbols.py BTC,ETH [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services import lane_registry as reg  # noqa: E402

LANE = "mm_asterdex"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", help="逗号分隔，如 BTC,ETH")
    ap.add_argument("--reason", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    new_syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not new_syms:
        print("✗ 空币种列表")
        return 2

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"✗ 车道不存在: {LANE}")
        return 2
    meta = dict(lane.get("meta") or {})
    old_syms = list(meta.get("symbols") or [])
    print(f"车道 {LANE}: symbols {old_syms} → {new_syms}")
    removed = [s for s in old_syms if s not in new_syms]
    added = [s for s in new_syms if s not in old_syms]
    if removed:
        print(f"  移出: {removed}（若仍有持仓，下一 tick 由 F90 孤儿逻辑平掉）")
    if added:
        print(f"  新增: {added}")
    if old_syms == new_syms:
        print("（无变化）")
        return 0
    if args.dry_run:
        print("--dry-run：未写入")
        return 0

    now_iso = datetime.now(timezone.utc).astimezone().isoformat()
    meta["symbols"] = new_syms
    ops = meta.get("ops_changes")
    if not isinstance(ops, list):
        ops = []
    ops.append({
        "ts": now_iso, "op": "set_symbols",
        "before": old_syms, "after": new_syms,
        "reason": args.reason or "F266 决策档案：BTC+ETH 9/9 不劣、8/9 严格更优（用户批准）",
    })
    meta["ops_changes"] = ops[-20:]
    ok = reg.update_meta(LANE, meta)
    print(f"update_meta: {ok}")
    after = (reg.get_lane(LANE) or {}).get("meta") or {}
    print(f"复核 symbols = {after.get('symbols')}")
    print("[注意] 需重启后端（runner 在重建时读 meta.symbols）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
