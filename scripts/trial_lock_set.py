# -*- coding: utf-8 -*-
"""[h652] 试跑互斥锁(trial_active)设置/清除 —— 方向层与宇宙层共用的治理互斥(§9.4)。

任一层的试跑未判决 ⇒ trial_active=true,另一层冻结宇宙且冻结参数;
判决完成后由判决方清标记。写入 lane_registry.meta.trial_active + ops_changes 审计。

用法:
    python scripts/trial_lock_set.py --on  --reason "D1 趋势闸试跑 11:45-23:50"
    python scripts/trial_lock_set.py --off --reason "D1 判决完成(KEEP/ROLLBACK)"
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", dest="on", action="store_true")
    ap.add_argument("--off", dest="on", action="store_false")
    ap.set_defaults(on=None)
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--reason", required=True)
    args = ap.parse_args()
    if args.on is None:
        print("必须给 --on 或 --off")
        return 2
    import psycopg

    # [h664 审计#6 修复] read-modify-write 必须套 h425 的跨进程文件锁
    # (_MetaLock,h429/h433 同款丢更新事故的既有解),否则并发写会互相覆盖。
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    with _h._MetaLock():
        with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s",
                        (args.lane,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {args.lane} 不存在")
                return 1
            meta = dict(row[0] or {})
            meta["trial_active"] = bool(args.on)
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "trial_lock_set", "op": "trial_active",
                        "value": bool(args.on),
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "reason": args.reason})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), args.lane))
            print(f"✓ trial_active = {bool(args.on)} ({args.reason})")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
