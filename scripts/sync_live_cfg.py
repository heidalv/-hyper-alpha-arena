# -*- coding: utf-8 -*-
"""[h665c 2026-10-01] 实盘配置同步:mm_asterdex(纸面) → mm_asterdex_live(实盘)。

用户要求:上实盘后,实盘跑的必须和模拟盘配置一样。
本脚本把纸面车道的 params/limits(同一 dict)/symbols/pattern_matrix/replay_baseline
复制到实盘车道;排除实盘专属键(compound_ratio 强制 0、live_caps/note/治理历史保留)。
与 /api/hft/live/control 的配置漂移硬闸配套:漂移时实盘无法启动,必须先跑本脚本。
"""
from __future__ import annotations

import io
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC_LANE = "mm_asterdex"
DST_LANE = "mm_asterdex_live"
# 从纸面复制到实盘的 meta 键
COPY_KEYS = ("params", "symbols", "pattern_matrix", "replay_baseline")
# params 里实盘专属:实盘权益来自交易所,复利必须为 0
PARAM_EXCLUDE = {"compound_ratio"}
# 实盘 meta 保留键(不被覆盖)
DST_KEEP_KEYS = {"live_caps", "note", "ops_changes", "trial_active",
                 "evolution", "stats_since", "h329v5_rollback", "h329_rollback",
                 "selection_note", "account_reset_at", "shadow_equity",
                 "replay_baseline"}  # replay_baseline 在 COPY_KEYS,这里不去重(后者覆盖)


def sync_flow_lists() -> int:
    """主动流只同步交易名单和入池时间。不复制做市挂宽、形态矩阵、compound_ratio。"""
    from backend.services import lane_registry as reg

    src = reg.get_lane(SRC_LANE) or {}
    dst = reg.get_lane(DST_LANE)
    if not dst:
        print(f"x 实盘车道 {DST_LANE} 不存在")
        return 1
    src_meta = dict(src.get("meta") or {})
    dst_meta = dict(dst.get("meta") or {})
    dst_meta["symbols"] = list(src_meta.get("symbols") or [])
    dst_meta["flow_admitted_at"] = dict(src_meta.get("flow_admitted_at") or {})
    dst_meta["flow_watch_pool"] = list(src_meta.get("flow_watch_pool") or [])
    from datetime import datetime, timezone
    ops = list(dst_meta.get("ops_changes") or [])
    ops.append({"by": "sync_flow_lists", "op": "symbols_only",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "symbols": dst_meta["symbols"]})
    dst_meta["ops_changes"] = ops[-40:]
    ok = reg.register_lane(
        lane_id=DST_LANE, mode="live",
        status=dst.get("status") or "stopped",
        meta=dst_meta)
    print(f"主动流名单同步: {ok}; symbols={dst_meta['symbols']}")
    return 0 if ok else 1


def main() -> int:
    import importlib.util

    from backend.services import lane_registry as reg

    src = reg.get_lane(SRC_LANE)
    dst = reg.get_lane(DST_LANE)
    if not src:
        print(f"x 纸面车道 {SRC_LANE} 不存在")
        return 1
    if not dst:
        print(f"x 实盘车道 {DST_LANE} 不存在(先跑 _create_live_lane.py)")
        return 1
    src_meta = dict(src.get("meta") or {})
    if float((src_meta.get("params") or {}).get("active_flow_mode") or 0.0) > 0:
        return sync_flow_lists()
    dst_meta = dict(dst.get("meta") or {})
    diff = []
    for k in COPY_KEYS:
        new_v = deepcopy(src_meta.get(k))
        old_v = dst_meta.get(k)
        dst_meta[k] = new_v
        if json.dumps(old_v, sort_keys=True, default=str) != \
                json.dumps(new_v, sort_keys=True, default=str):
            diff.append(k)
    # 实盘专属修正:复利 0
    params = dict(dst_meta.get("params") or {})
    params["compound_ratio"] = 0.0
    dst_meta["params"] = params
    # 治理历史保留:ops_changes 追加本次同步记录
    ops = list(dst_meta.get("ops_changes") or [])
    from datetime import datetime, timezone
    ops.append({"by": "sync_live_cfg", "op": "cfg_sync", "from": SRC_LANE,
                "changed": diff,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    dst_meta["ops_changes"] = ops[-40:]
    # 跨进程锁(与 h425 同款)
    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    with _h._MetaLock():
        ok = reg.register_lane(
            lane_id=DST_LANE, mode="live",
            status=dst.get("status") or "stopped",
            meta=dst_meta)
    print(f"写入: {ok}; 变更键: {diff if diff else '(无,已一致)'}")
    # 复核
    d2 = reg.get_lane(DST_LANE) or {}
    p2 = dict((d2.get("meta") or {}).get("params") or {})
    print(f"实盘现 symbols={d2.get('meta', {}).get('symbols')}")
    print(f"实盘现 params 键数={len(p2)} compound_ratio={p2.get('compound_ratio')}")
    print(f"实盘现 live_caps 保留={bool((d2.get('meta') or {}).get('live_caps'))}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
