"""h463/h464 参数与试跑元数据探针（只读，autocommit）。

用法：python scripts/h463_param_probe.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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
KEYS = [
    "max_one_side_seconds", "ofi_confirm_threshold", "trend_only_q", "trend_only_bp",
    "stop_loss_bp", "stop_ref_last_leg", "trail_lock_bp", "post_stop_decay",
    "p1_hold_sec", "p45_hold_sec", "ofi_flatten_threshold", "compound_ratio",
    "timeout_hard_taker_sec", "exit_skew_k", "vol_spread_k",
]


def main() -> int:
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print("lane_registry 无记录")
                return 1
            m = row[0]
            p = dict(m.get("params") or {})
            for k in KEYS:
                print(f"{k:26s} = {p.get(k)}")
            print("-" * 46)
            for k in ("h462_trial", "h463_trial", "h464_trial"):
                v = m.get(k)
                print(f"{k}: {json.dumps(v, ensure_ascii=False) if v else '—'}")
            print("-" * 46)
            print("account_reset_at =", m.get("account_reset_at"))
            cur.execute(
                "SELECT state_json FROM lane_runtime_state WHERE lane_id=%s", (LANE,))
            r2 = cur.fetchone()
            if r2:
                st = r2[0] or {}
                rt = dict(st.get("params") or {})
                diff = [k for k in KEYS if k in rt and rt.get(k) != p.get(k)]
                print("runtime 生效值（关键项）:",
                      {k: rt.get(k) for k in ("max_one_side_seconds",
                                               "ofi_confirm_threshold")})
                print("runtime vs registry 不一致:", diff or "无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
