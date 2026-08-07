"""落地交易调优：基于 ai_reverse / mid-swing 失血归因的小步参数调整。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend.services.runtime_governor import runtime_governor
from backend.services.runtime_tuning_store import apply_patches, get_all_tuning, invalidate_cache


def main() -> int:
    before = get_all_tuning()
    print("=== 调优前 ===")
    for k in (
        "maturity_global_n1",
        "scalp_min_confidence",
        "master_reduce_min_loss_pct",
    ):
        print(f"  {k}: {before.get(k)}")

    swing_before = (before.get("by_nature") or {}).get("swing", {})
    print(f"  by_nature.swing.min_confidence: {swing_before.get('min_confidence')}")

    # Governor 受管 key（±20% 内小步）
    patches_gov = {
        "maturity_global_n1": 60,       # 51 → 60（上限，+17.6%）减少未成熟策略低质量入场
        "scalp_min_confidence": 75,     # 70 → 75（+7.1%）抬高短线门槛
    }
    for key, val in patches_gov.items():
        r = runtime_governor.submit_intent(
            key, val,
            source="manual",
            confidence=0.85,
            reason="ai_reverse失血归因：低置信度入场+mid/swing偏弱，小步收紧",
            ttl_sec=None,
        )
        print(f"[Governor] {key}={val} applied={r.get('applied')} winner={r.get('winner_source')}")

    # 非受管 key 直写 runtime_tuning.json
    swing_cfg = dict((before.get("by_nature") or {}).get("swing") or {})
    swing_cfg["min_confidence"] = 60
    by_nature = dict(before.get("by_nature") or {})
    by_nature["swing"] = swing_cfg

    direct = apply_patches({
        "master_reduce_min_loss_pct": 0.096,  # 0.08 → 0.096 (+20%)
        "by_nature": by_nature,
    }, proposal_id=None)
    invalidate_cache()
    print(f"[Direct] applied: {direct}")

    after = get_all_tuning()
    print("\n=== 调优后 ===")
    for k in (
        "maturity_global_n1",
        "scalp_min_confidence",
        "master_reduce_min_loss_pct",
    ):
        print(f"  {k}: {after.get(k)}")
    swing_after = (after.get("by_nature") or {}).get("swing", {})
    print(f"  by_nature.swing.min_confidence: {swing_after.get('min_confidence')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
