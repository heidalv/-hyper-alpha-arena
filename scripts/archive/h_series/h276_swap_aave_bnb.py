# -*- coding: utf-8 -*-
"""H276 换币：AAVE（18 腿 −8.68bp、−$3.36，占本时代亏损 100%）→ BNB。

# 依据（07:34 实测，05:34 起 90 分钟）

  ETH  154 腿 −0.373bp   SOL 63 腿 −1.050bp   DOGE 39 腿 **+1.710bp**   AAVE 18 腿 **−8.683bp**
  exit_path：TP 11 腿 +21.28bp / maker 257 腿 −0.315bp / SL 6 腿 −38.45bp
  ⇒ AAVE 一条腿把整个时代拖负（−3.36 / −3.2 合计）；其余三币合计 ≈ 持平。
  BNB：反转 corr −0.164、trades 425/h、步长表内（step 0.01，min_notional 5），
      单腿 $300 ≈ 0.5 BNB ✓。

# 用法

    python scripts/h276_swap_aave_bnb.py            # 换币 + 验证热采用
    python scripts/h276_swap_aave_bnb.py --rollback
    python scripts/h276_swap_aave_bnb.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h276_swap_aave_bnb_state.json"
TARGET = ["SOL", "DOGE", "ETH", "BNB"]
NOTE = ("H276：AAVE（18 腿 −8.68bp、−$3.36，本时代唯一大出血）→ BNB（corr −0.164、425 trades/h）。"
        "宇宙=[SOL, DOGE, ETH, BNB]。")


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
    print("H276  换币：AAVE（−8.68bp 出血点）→ BNB")
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
        "reason": "AAVE 18腿−8.68bp/−$3.36（本时代亏损100%）→ BNB（corr −0.164、425 trades/h）",
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
