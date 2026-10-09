# -*- coding: utf-8 -*-
"""H275 时代调优（一轮两个改动，各留回滚快照）：

  ① 宇宙：AVAX → DOGE
     · AVAX 换入后 9 腿 −5.29bp（−$1.01），price −2.77bp，LLM 判定 replace；
     · DOGE：反转 corr −0.154、trades 864/h、SYMBOL_STEP 表内（step 1.0）✓
  ② 参数：vol_pause_sigma 0.7 → 1.0
     · 车道级波动暂停在旧时代触发 399 次，是最大成交压制源；
     · H257：反转 alpha 随 |趋势| 单调增强 —— 波动放大正是反转最值钱的时刻，
       0.7σ 就把这些时刻全停了；1.0σ 放行，极端急动仍有 sudden_move 20bp 闸兜底。
     · LLM 监控连续 8 轮建议 0.7→0.9~1.0。

# 用法

    python scripts/h275_era_tuning.py            # 应用两项 + 验证热采用
    python scripts/h275_era_tuning.py --rollback
    python scripts/h275_era_tuning.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h275_era_tuning_state.json"
TARGET_SYMS = ["SOL", "DOGE", "ETH", "AAVE"]
TARGET_PARAMS = {"vol_pause_sigma": 1.0}
NOTE = ("H275：AVAX（9 腿 −5.29bp）→ DOGE（corr −0.154、864 trades/h）。"
        "vol_pause_sigma 0.7→1.0（旧时代 399 次暂停压制反转黄金窗口）。")


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


def read_state() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols', meta_json->'params'"
                        " FROM lane_registry WHERE lane_id=%s", (LANE,))
            r = cur.fetchone()
    return {"symbols": list(r[0] or []) if r else [], "params": dict(r[1] or {}) if r else {}}


def write_symbols(syms: list) -> None:
    import psycopg
    now_iso = dt.datetime.now().astimezone().isoformat()
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                        " '{symbols}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                        (json.dumps(syms), LANE))
            cur.execute("UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                        " '{universe,ai}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                        (json.dumps(syms), LANE))
            cur.execute("UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                        " '{universe,note}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                        (json.dumps(NOTE), LANE))
            cur.execute("UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                        " '{universe,as_of}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                        (json.dumps(now_iso), LANE))
        c.commit()


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


def live_state() -> dict:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {"symbols": [], "limits": {}}
    d = {"symbols": list(j.get("symbols") or []), "limits": dict(j.get("limits") or {})}
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    cur = read_state()
    print("=" * 96)
    print("H275  时代调优：AVAX → DOGE + vol_pause_sigma 0.7 → 1.0")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        write_symbols(snap["symbols"])
        write_params(snap["params"])
        t0 = time.time()
        while time.time() - t0 < 150:
            live = live_state()
            ok_syms = set(live["symbols"]) == set(snap["symbols"])
            ok_params = all(abs(float(live["limits"].get(k) or 0) - float(snap["params"].get(k) or 0)) < 1e-9
                            for k in snap["params"])
            if ok_syms and ok_params:
                STATE.unlink(missing_ok=True)
                print("  ✓ 已恢复")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认")
        return 1

    if a.status:
        print(f"  宇宙注册表: {cur['symbols']}  实盘: {live_state()['symbols']}")
        print(f"  vol_pause_sigma 注册表: {cur['params'].get('vol_pause_sigma')}"
              f"  实盘: {live_state()['limits'].get('vol_pause_sigma')}")
        return 0

    print(f"  宇宙: {cur['symbols']} → {TARGET_SYMS}")
    print(f"  参数: vol_pause_sigma {cur['params'].get('vol_pause_sigma')} → {TARGET_PARAMS['vol_pause_sigma']}")
    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "symbols": cur["symbols"],
        "params": {k: cur["params"].get(k) for k in TARGET_PARAMS},
        "reason": "AVAX 9腿−5.29bp→DOGE；vol_pause 399次压制反转窗口→1.0σ",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 快照存 {STATE}")
    write_symbols(TARGET_SYMS)
    write_params(TARGET_PARAMS)
    print("  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_state()
        ok_syms = set(live["symbols"]) == set(TARGET_SYMS)
        ok_params = abs(float(live["limits"].get("vol_pause_sigma") or 0) - 1.0) < 1e-9
        if ok_syms and ok_params:
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：宇宙={live['symbols']}"
                  f"  vol_pause_sigma={live['limits'].get('vol_pause_sigma')}")
            return 0
        time.sleep(5)
    live = live_state()
    print(f"  ⚠️ 150s 未确认（宇宙={live['symbols']} σ={live['limits'].get('vol_pause_sigma')}）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
