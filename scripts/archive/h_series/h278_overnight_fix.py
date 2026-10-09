# -*- coding: utf-8 -*-
"""H278 隔夜复盘后的两个参数修复（各留回滚快照）。

# 依据（隔夜 23:00→09:38 全量账本分析）

  1. stop_maker_grace_sec 120 → 30
     H273 前后对比（同一口径）：
       grace=0  ：SL 40 腿 −15.82bp / TP 11 腿 +5.20bp
       grace=120：SL 17 腿 **−32.35bp** / TP 38 腿 +12.24bp
     ⇒ 宽限 120s 让止盈数量 11→38（好，由 take_profit_maker_grace_sec 管，不动），
       但止损腿深度翻倍（−15.8→−32.4bp，最深 −65bp）：止损触发后多扛 2 分钟，
       趋势里价格再跑 20~50bp 才 taker 兜底。收到 30s：保留"先 maker 试 30s"的
       免费出场机会，封住趋势段深度失控。止损数量已从 40→17（宽限本身有价值），
       30s 不至于让数量回弹太多。

  2. side_trend_min_bp 0 → 3
     早晨趋势段（06:00–09:38）实测：ETH wprice −1.14bp、SOL −1.11bp，
     小趋势噪声单是负贡献；而 DOGE（大波动币）wprice +2.51bp 依旧正。
     H257：反转 alpha 随 |趋势| 单调增强 ⇒ 只做 |120s 趋势| ≥ 3bp 的腿，
     过滤早晨/盘整期的小趋势噪声单，保留强趋势后的高 alpha 段。

# 用法

    python scripts/h278_overnight_fix.py            # 应用 + 验证热采用
    python scripts/h278_overnight_fix.py --rollback
    python scripts/h278_overnight_fix.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h278_overnight_fix_state.json"
TARGET = {"stop_maker_grace_sec": 30.0, "side_trend_min_bp": 3.0}
KEYS = ["stop_maker_grace_sec", "side_trend_min_bp"]


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
    print("H278  隔夜复盘修复：止损宽限 120→30 + 趋势门槛 0→3bp")
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
        "reason": "隔夜复盘：SL 深度 −15.8→−32.4bp（宽限 120s 趋势段失控）收 30s；"
                  "早晨小趋势噪声单负贡献 → side_trend_min_bp=3",
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
