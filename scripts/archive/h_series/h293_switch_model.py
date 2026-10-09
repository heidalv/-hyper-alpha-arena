# -*- coding: utf-8 -*-
"""H293 切换模型模式：side_mode counter_trend → model。

# 依据（影子对照，48h，同一衰减出场 P3）
    lookback 90（现行）:  净 −0.794bp/腿  MAE −1.743
    lookback 60（模型）:  净 −0.645bp/腿  MAE −1.485  ← 更优
    另 H280 双窗：k=60 信号强于 k=90 的日窗样本；工件 ic_val 0.051（发布条件已过）。
# 生效逻辑（runner F347）：
    side_mode="model" → 读 meta.ai_model（r60 单特征）→ 趋势信号 = ±r60（权重符号）
    → |r60| ≥ 0.25·sd(≈3.1bp) 才挂单，否则 skip=model_below_thr；
    工件缺失/异常 → 静默回退 counter_trend。出场/闸门全部不变。

# 用法
    python scripts/h293_switch_model.py            # 切换 + 验证热采用
    python scripts/h293_switch_model.py --rollback
    python scripts/h293_switch_model.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h293_switch_model_state.json"
KEYS = ["side_mode"]
TARGET = {"side_mode": "model"}


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


def write_param(key: str, val) -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " %s::text[], to_jsonb(%s::text), true), updated_at=now()"
                " WHERE lane_id=%s", (f"{{params,{key}}}", val, LANE))
        c.commit()


def live_mode() -> str:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        return str((j.get("params") or {}).get("side_mode") or "")
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    cur = read_params()
    print("=" * 96)
    print("H293  切换模型模式（counter_trend → model）")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_param("side_mode", orig.get("side_mode", "counter_trend"))
        t0 = time.time()
        while time.time() - t0 < 150:
            if live_mode() == orig.get("side_mode", "counter_trend"):
                STATE.unlink(missing_ok=True)
                print(f"  ✓ 已恢复 {orig}")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认")
        return 1

    if a.status:
        print(f"  注册表: {cur.get('side_mode')}  实盘: {live_mode()}")
        return 0

    print(f"  当前: {cur.get('side_mode')}")
    print(f"  目标: {TARGET['side_mode']}")
    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": {"side_mode": cur.get("side_mode")},
        "reason": "影子对照：60s 模型 −0.645bp > 90s 规则 −0.794bp（同衰减出场）",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 快照存 {STATE}")
    write_param("side_mode", TARGET["side_mode"])
    print("  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        if live_mode() == TARGET["side_mode"]:
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：side_mode={live_mode()}")
            return 0
        time.sleep(5)
    print(f"  ⚠️ 150s 未确认（实盘 {live_mode()}）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
