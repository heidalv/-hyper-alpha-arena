# -*- coding: utf-8 -*-
"""H290 信号时机升级：trend_skew_lookback 8 期(120s) → 6 期(90s)。

# 依据（H280 双窗口交叉验证，48h × 2）

  窗口1（最近 48h）:  k=120→m=60 corr −0.0165 (t−1.7)   | k=60→m=60 −0.0424 | k=90→m=60 −0.0357
  窗口2（2~4 天前）:  k=120→m=60 corr −0.0169 (t−1.8)   | k=90→m=60 −0.0327 | k=90→m=90 −0.0438
  ⇒ 两窗口一致：90s 回看比 120s 强 ~2 倍（k=90→m=60 两窗都 −0.03x）；
     k=60 在旧窗衰减（−0.009），不稳。90s 是跨窗最稳的最强格。
  持有窗口：fwd 60~90s 是 alpha 主体（与 max_one_side_seconds=90 一致，不动）。

# 用法

    python scripts/h290_apply_timing.py            # 应用 + 验证热采用
    python scripts/h290_apply_timing.py --rollback
    python scripts/h290_apply_timing.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h290_apply_timing_state.json"
KEYS = ["trend_skew_lookback"]
TARGET = {"trend_skew_lookback": 6.0}


def dsn() -> str:
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


def read_params() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            return dict(cur.fetchone()[0] or {})


def write_params(patch: dict) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            for key, val in patch.items():
                cur.execute(
                    "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                    " %s::text[], to_jsonb(%s::float8), true), updated_at=now()"
                    " WHERE lane_id=%s", (f"{{params,{key}}}", float(val), LANE))
        c.commit()


def live_params() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    d = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        d.setdefault(k, v)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    cur = read_params()
    print("=" * 96)
    print("H290  信号时机升级：趋势回看 120s → 90s（H280 双窗交叉验证）")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_params(orig)
        t0 = time.time()
        while time.time() - t0 < 150:
            live = live_params()
            if all(abs(float(live.get(k) or 0) - float(orig.get(k) or 0)) < 1e-9 for k in KEYS):
                STATE.unlink(missing_ok=True)
                print(f"  ✓ 已恢复 { {k: orig.get(k) for k in KEYS} }")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认")
        return 1

    if a.status:
        print(f"  注册表: { {k: cur.get(k) for k in KEYS} }")
        print(f"  实盘:   { {k: live_params().get(k) for k in KEYS} }")
        return 0

    print(f"  当前: { {k: cur.get(k) for k in KEYS} }")
    print(f"  目标: {TARGET}")
    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": {k: cur.get(k) for k in KEYS},
        "reason": "H280 双窗验证：k=90s→m=60s corr −0.036/−0.033 vs 120s 的 −0.016/−0.017，2× 强度",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 快照存 {STATE}")
    write_params(TARGET)
    print("  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_params()
        if all(abs(float(live.get(k) or 0) - TARGET[k]) < 1e-9 for k in KEYS):
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：{ {k: live.get(k) for k in KEYS} }")
            return 0
        time.sleep(5)
    print(f"  ⚠️ 150s 未确认（实盘 { {k: live_params().get(k) for k in KEYS} }）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
