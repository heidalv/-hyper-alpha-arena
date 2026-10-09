# -*- coding: utf-8 -*-
"""H306 移除 SOL：5000 时代持续唯一出血币。

# 依据（LLM 评审 8 轮 + 账本，00:26–02:42）：
    SOL 逐 30min 净额持续 −3.0~−4.1bp/腿（price_bp −3.2~−4.3，纯方向性亏损），
    每晚各轮 LLM 全部判 replace；ETH/BNB/DOGE 合计 ≈ 打平到微正。
    且 SOL 多次出现单边裸露持仓（79.17 多头只挂 ask）。
    5000 时代累计：SOL −$18~−$13/30min 级别拖累，是唯一的稳定负贡献腿。
# 动作：宇宙 [SOL, DOGE, ETH, BNB] → [DOGE, ETH, BNB]（SOL 持仓走 orphan 平仓）。

# 用法
    python scripts/h306_remove_sol.py            # 移除 + 验证热采用
    python scripts/h306_remove_sol.py --rollback
    python scripts/h306_remove_sol.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h306_remove_sol_state.json"
TARGET = ["DOGE", "ETH", "BNB"]
NOTE = ("H306：SOL 在 5000 时代持续 −3~−4bp/腿（唯一稳定出血币，LLM 8 轮判 replace）"
        "→ 移除。宇宙=[DOGE, ETH, BNB]。")


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
    print("H306  移除 SOL（唯一稳定出血币）")
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
    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": before, "target": TARGET,
        "reason": "SOL 5000 时代持续 −3~−4bp/腿，LLM 8 轮判 replace",
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
