# -*- coding: utf-8 -*-
"""H267 P2 退出改造：把退出从"做市等对手盘"改成"反转交易落袋"。

# 验证依据（全部已跑完，不是猜）

  H264 持有期曲线：逆势收益 30s +0.22 / 60s **+0.34** / 120s +0.09 /
                   300s **−0.16** / 600s −0.48 ⇒ 持有 60s 最优，300s 转负
  H264 分布（H=60s）：P75 +6.57bp（反转到位）、P25 −5.99bp（趋势继续）
  H266 反转时平仓 +5.30bp vs 固定持有 −1.10bp ⇒ 退出要"反转时落袋"

# 三个改动（都是"退出节奏"这一件事，作为 P2 整体改）

  max_one_side_seconds  7200 → **120**   持有 2 小时是拿反转扛趋势（灾难）
  take_profit_bp          12 → **5**     反转中位 +3.13bp，12bp 等不到
  stop_loss_bp            80 → **6**     趋势继续 ~6bp 就该止损，80bp 太宽

# 语义核对（counter_trend 做空为例）

  做空（qty<0）：价格回落=反转=浮盈 → take_profit 5bp 落袋 ✓
                价格继续涨=趋势延续=浮亏 → stop_loss 6bp 止损 ✓
  减仓侧（买）挂单被动成交优先于 taker（take_profit_maker_grace_sec=120 已有）

# 回滚

  快照存 logs/h267_p2_exit_rollback.json，--rollback 恢复

# 用法

    python scripts/h267_p2_exit_rework.py            # 改 + 验证
    python scripts/h267_p2_exit_rework.py --rollback
    python scripts/h267_p2_exit_rework.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h267_p2_exit_rollback.json"
KEYS = ("max_one_side_seconds", "take_profit_bp", "stop_loss_bp")
TARGET = {"max_one_side_seconds": 120.0, "take_profit_bp": 5.0,
          "stop_loss_bp": 6.0}


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
    print("H267  P2 退出改造（做市节奏 → 反转节奏）")
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
        "reason": "P2 退出改造：反转交易持有 120s、止盈 5bp、止损 6bp",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 回滚快照存 {STATE}")
    write_params(TARGET)
    print(f"  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_params()
        if int(live.get("max_one_side_seconds") or 0) == 120:
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）")
            print(f"     max_one_side_seconds={live.get('max_one_side_seconds')} "
                  f"take_profit_bp={live.get('take_profit_bp')} "
                  f"stop_loss_bp={live.get('stop_loss_bp')}")
            return 0
        time.sleep(5)
    print("  ⚠️ 150s 未确认生效")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
