# -*- coding: utf-8 -*-
"""H279 提频三连（用户要求：不做质量验证等待，直接提高交易频率）。

# 依据（频率诊断 09:50）

  · 完整开平周期仅 ~7 往返/时（4 币合计），理论上限 ~90；腿数不低但周期少。
  · skip_counts：symbol_exposure 425 / vol_pause 344 / 趋势闸 285+218 —— 入场被闸门压死。
  · 结构：counter_trend 单边挂单 + 120s 持有 ⇒ 每币 30~40 分钟一轮。

# 三个频率旋钮（都可回滚）

  1. side_trend_min_bp  3 → 0   （撤销 H278 的趋势门槛；不做质量测试等待，直接放行全部趋势档）
  2. max_one_side_seconds 120 → 90（持有周期缩短 25%，反转 300s 衰减、60~120s 是甜区）
  3. vol_pause_sigma 1.0 → 1.5（4.2 小时被波动暂停 344 次；1.5σ 放行，急动仍有 sudden_move 20bp 兜底）

# 用法

    python scripts/h279_freq_boost.py            # 应用 + 验证热采用
    python scripts/h279_freq_boost.py --rollback
    python scripts/h279_freq_boost.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h279_freq_boost_state.json"
TARGET = {"side_trend_min_bp": 0.0, "max_one_side_seconds": 90.0, "vol_pause_sigma": 1.5}
KEYS = ["side_trend_min_bp", "max_one_side_seconds", "vol_pause_sigma"]


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
    print("H279  提频三连：趋势门槛 0 + 持有 90s + 波动暂停 1.5σ")
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
        "reason": "提频：撤销趋势门槛、持有 120→90s、波动暂停 1.0→1.5σ（用户要求直接提频不做质量测试）",
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
