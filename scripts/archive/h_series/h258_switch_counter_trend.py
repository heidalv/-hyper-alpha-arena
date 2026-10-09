# -*- coding: utf-8 -*-
"""F343 切换 counter_trend：只逆势挂单（涨了卖、跌了买），利用短期反转 alpha。

# 三层证据（全部跨窗口稳定，本会话最硬的信号）

  H255（市场 tick，12h）：短期反转 corr 在 30s~5min 尺度 4/4 币为负，
      9 个 (k,m) 组合一致；最强 SOL past120s→fwd60s = −0.0705
  H256（账本+tick，12h）：lookback=120s 逆势 +0.84 / 顺势 −2.38 / 差 3.22bp，
      **13/13 小时逆势优于顺势**；lookback=60s 13/13 也一致
  H257（幅度依赖）：逆势−顺势差随 |趋势| 单调：+1.32 → +1.55 → +1.68
      → +2.75 → +5.15 → +7.78bp；最小档（0–2bp）逆势也是正的（+0.199、73.7%）

# 框架纠偏（本会话最关键的认知修正）

此前把 lane 当「双边做市」测 spread−逆选择 ⇒ 得出"结构性负期望"是**错的**。
正确框架是**方向性短期反转交易**：过去 120s 涨了 ⇒ 未来大概率回落 ⇒ 只挂卖；
跌了 ⇒ 只挂买。逆势腿（+0.84bp）和顺势腿（−2.38bp）之前混在 `both` 里互相抵消。

# 参数（三处，都在 meta.params）

  side_mode            : both → **counter_trend**
  trend_skew_lookback  : 60   → **8**（= 8×15s = 120s，H256 最强尺度）
  side_trend_min_bp    : 10   → **0**（全档单边；H257 最小档也反转）

# 回滚

本脚本先写 `logs/f343_counter_trend_rollback.json` 快照，
任何时刻可 `--rollback` 恢复。

# 用法

    python scripts/h258_switch_counter_trend.py            # 切换 + 验证
    python scripts/h258_switch_counter_trend.py --rollback # 恢复
    python scripts/h258_switch_counter_trend.py --status   # 查看
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "f343_counter_trend_rollback.json"
KEYS = ("side_mode", "trend_skew_lookback", "side_trend_min_bp")
TARGET = {"side_mode": "counter_trend", "trend_skew_lookback": 8,
          "side_trend_min_bp": 0.0}


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
                    " %s::text[], to_jsonb(%s::text), true), updated_at=now()"
                    " WHERE lane_id=%s", (f"{{params,{key}}}", val, LANE))
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
    print("F343  counter_trend 切换（短期反转 alpha）")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无回滚快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_params(orig)
        print(f"  已写回 {orig}")
        t0 = time.time()
        while time.time() - t0 < 150:
            live = live_params()
            if all(live.get(k) == orig[k] for k in KEYS):
                STATE.unlink(missing_ok=True)
                print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：已恢复 {orig}")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认生效，请人工检查")
        return 1

    if a.status:
        print(f"  注册表: { {k: cur.get(k) for k in KEYS} }")
        print(f"  实盘:   { {k: live_params().get(k) for k in KEYS} }")
        return 0

    # 切换
    print(f"  当前: { {k: cur.get(k) for k in KEYS} }")
    print(f"  目标: {TARGET}")
    STATE.write_text(json.dumps({
        "saved_at": __import__("datetime").datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": {k: cur.get(k) for k in KEYS},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 回滚快照已存 {STATE}")
    write_params(TARGET)
    print(f"  已写入，等待热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_params()
        if live.get("side_mode") == "counter_trend" and \
                int(live.get("trend_skew_lookback") or 0) == 8:
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）")
            print(f"     side_mode={live.get('side_mode')} "
                  f"lookback={live.get('trend_skew_lookback')} "
                  f"min_bp={live.get('side_trend_min_bp')}")
            return 0
        time.sleep(5)
    print("  ⚠️ 150s 未确认生效")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
