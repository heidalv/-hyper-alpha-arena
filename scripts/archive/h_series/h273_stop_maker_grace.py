# -*- coding: utf-8 -*-
"""H273 止损出口对称化：stop_maker_grace_sec 0 → 120。

# 依据（新时代 exit_path 分布，01:27 重置后实测）

  maker_entry      33 腿  +2.753bp  fee 0.0000   ← 入场是赚的（逆势报价有效）
  take_profit_taker  3 腿  +3.779bp  fee −2.200
  stop_loss_taker   21 腿  **−8.603bp**  fee −2.493  ← 亏损全部来自这里
  orphan_taker       2 腿  +4.823bp

  ⇒ 21 次止损 vs 3 次止盈。6bp 止损一触发就 taker：付 4bp 费 + 吃半价差 + 滑点，
     平均 −8.6bp/腿；而止盈上限 +5bp。结构性「赚小亏多」。

# 为什么改成 maker 先挂 120s 是反转框架的正确做法

  · H257：反转幅度随 |趋势| 单调递增 —— 6bp 逆势后正是**反转概率最高**的时刻，
    立即 taker 离场 = 在最不该卖的时刻付费卖（反 alpha）。
  · H266：反转离场 +5.30bp vs 固定持有 −1.10bp —— 离场应由趋势反转驱动，
    而不是固定价格止损砸单。
  · counter_trend 本来只挂逆势侧：止损触发后减仓侧 maker 单照常挂着，
    120s 内被对手方接走 = 0 手续费离场；若真的一路狂奔，120s 后仍可 taker 兜底。
  · 与已有 take_profit_maker_grace_sec=120 对称（止盈早已是先 maker 后 taker）。

# 用法

    python scripts/h273_stop_maker_grace.py            # 应用 + 验证热采用
    python scripts/h273_stop_maker_grace.py --rollback
    python scripts/h273_stop_maker_grace.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h273_stop_maker_grace_state.json"
KEYS = ["stop_maker_grace_sec"]
TARGET = {"stop_maker_grace_sec": 120.0}


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
    print("H273  止损出口对称化（stop_maker_grace_sec 0 → 120）")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无回滚快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_params(orig)
        t0 = time.time()
        while time.time() - t0 < 150:
            live = live_params()
            if all(live.get(k) == orig[k] for k in KEYS):
                STATE.unlink(missing_ok=True)
                print(f"  ✓ 已恢复 {orig}")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认生效")
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
        "reason": "止损出口对称化：先 maker 挂 120s（0 费）再 taker 兜底，对标止盈的 grace",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 回滚快照存 {STATE}")
    write_params(TARGET)
    print("  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_params()
        if all(abs(float(live.get(k) or 0) - TARGET[k]) < 1e-9 for k in KEYS):
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：{ {k: live.get(k) for k in KEYS} }")
            return 0
        time.sleep(5)
    print(f"  ⚠️ 150s 未确认生效（实盘 { {k: live_params().get(k) for k in KEYS} }）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
