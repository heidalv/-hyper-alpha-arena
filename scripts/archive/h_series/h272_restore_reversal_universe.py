# -*- coding: utf-8 -*-
"""H272 恢复反转宇宙：把 01:19:03 被残留选择器任务覆写回 ASTER 的宇宙，
按 H125 反转 corr（pwsh-301，2h tick 窗口，01:31 跑完）重设为 top-4。

依据（pwsh-301 --metric reversal 输出）：
  PENDLE −0.26325 / AVAX −0.21512 / ETH −0.20329 / ENA −0.20217 / AAVE −0.18301 / SUI −0.17275
  XRP +0.0234、HYPE +0.0387、ASTER −0.0493 ⇒ 全部摘除
  ENA 缺 SYMBOL_STEP 步长表（F344 未纳入）⇒ 用 AAVE 替补（−0.18301，表内 ✓）

注意事项：
  · 现持仓 ASTER −413.57 / HYPE +2.84 会被移除出宇宙 ⇒ 走 orphan 平仓（taker 4bp，
    名义 ~$300 ⇒ 费用 ~$0.12，纸面实验可接受；本脚本记录原仓用于回滚说明）。
  · 不碰 stats_since（01:27:08 前端重置后的时代保持连续，收益记录不断代）。
  · 选择器定时任务保持禁用（pwsh-301 的 --apply 需 40+ 分钟且会覆写，实验期不用）。

用法：
    python scripts/h272_restore_reversal_universe.py            # 换宇宙 + 验证热采用
    python scripts/h272_restore_reversal_universe.py --rollback
    python scripts/h272_restore_reversal_universe.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h272_reversal_universe_state.json"
TARGET = ["PENDLE", "AVAX", "ETH", "AAVE"]
NOTE = ("H125 反转 corr top-4（01:31 实测：PENDLE −0.263/AVAX −0.215/ETH −0.203/AAVE −0.183；"
        "ENA −0.202 缺步长表故用 AAVE 替补；XRP +0.023/HYPE +0.039 为动量无反转已摘除）")


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


def read_symbols() -> list:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
    return list(r[0] or []) if r else []


def write_symbols(syms: list) -> None:
    import psycopg
    now_iso = dt.datetime.now().astimezone().isoformat()
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " '{symbols}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                (json.dumps(syms), LANE))
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " '{universe,ai}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                (json.dumps(syms), LANE))
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " '{universe,note}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                (json.dumps(NOTE), LANE))
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " '{universe,as_of}', %s::jsonb, true), updated_at=now() WHERE lane_id=%s",
                (json.dumps(now_iso), LANE))
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " '{universe,net_bp_per_cycle}', '{}'::jsonb, true),"
                " updated_at=now() WHERE lane_id=%s", (LANE,))
        c.commit()


def live_symbols() -> list:
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        return list(j.get("symbols") or [])
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    before = read_symbols()
    print("=" * 96)
    print("H272  恢复反转宇宙（残留选择器任务在 01:19:03 把 PENDLE 覆写回了 ASTER）")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无快照")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_symbols(orig)
        t0 = time.time()
        while time.time() - t0 < 150:
            if set(live_symbols()) == set(orig):
                STATE.unlink(missing_ok=True)
                print(f"  ✓ 已恢复 {orig}")
                return 0
            time.sleep(5)
        print("  ⚠️ 150s 未确认")
        return 1

    if a.status:
        print(f"  注册表: {before}")
        print(f"  实盘:   {live_symbols()}")
        return 0

    print(f"  当前: {before}")
    print(f"  目标: {TARGET}")
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        states = j.get("states") or {}
        held = {s: float((states.get(s) or {}).get("qty") or 0) for s in states
                if abs(float((states.get(s) or {}).get("qty") or 0)) > 1e-9}
        if held:
            print(f"  ⚠️ 现持仓 {held} —— 被移除的币将走 orphan 平仓（taker 4bp，纸面可接受）")
    except Exception as e:
        print(f"  （读持仓失败，忽略）: {e}")

    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": before, "target": TARGET,
        "reason": "H125 反转 corr top-4 恢复；XRP/HYPE 动量无反转、ASTER 反转最弱全部摘除",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 快照存 {STATE}")
    write_symbols(TARGET)
    print("  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_symbols()
        if set(live) == set(TARGET):
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：{live}")
            return 0
        time.sleep(5)
    print(f"  ⚠️ 150s 未确认生效（实盘 {live_symbols()}）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
