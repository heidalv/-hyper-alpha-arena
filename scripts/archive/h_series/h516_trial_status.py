"""h516：议程相关的**试跑状态与参数现值**一览（只读，供上线前/判定前对照）。

用法：python scripts/h516_trial_status.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from scripts.h425_repair_trial import LANE, read_env_dsn  # noqa: E402

KEYS = ("h425_trial", "h435_trial", "h436_trial", "h437_trial", "h438_trial",
        "h441_trial", "h442_trial", "h443_trial", "h448_trial", "h452_trial",
        "h454_trial", "h463_trial", "h464_trial", "h472_trial")


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = cur.fetchone()[0]
    p = dict(meta.get("params") or {})
    print("关键参数现值：")
    for k in ("ofi_confirm_threshold", "trend_only_q", "trend_only_bp",
              "max_one_side_seconds", "max_quote_age_sec", "stop_maker_grace_sec",
              "compound_ratio", "ofi_flatten_maker_only", "stop_loss_bp",
              "stop_ref_last_leg", "trail_lock_bp", "post_stop_decay",
              "p1_hold_sec", "p45_hold_sec"):
        print(f"  {k:26s} = {p.get(k)}")
    print("\n试跑状态：")
    for k in KEYS:
        t = meta.get(k)
        if not t:
            continue
        v = t.get("verdict") or "(未判定)"
        by = f" by={t.get('superseded_by')}" if t.get("superseded_by") else ""
        ja = (t.get("judge_at") or "")[:16]
        print(f"  {k:12s} {v:12s}{by:16s} judge_at={ja}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
