# -*- coding: utf-8 -*-
"""H274 P1b 换币：PENDLE（2 小时 0 成交，tick 率不足）→ SOL（实测有流+反转 corr）。

# 依据

  · H271 P1 观测 120 分钟：PENDLE **0 腿**（book 更新有、真实 trades <363/h≈0.1/s）
  · 最近 60min asterdex_trades：ETH 2220 / SOL 890 / DOGE 864 / BNB 425 /
    ASTER 740 / HYPE 1415（动量✗）/ XRP 1491（动量✗）/ ZEC 1496（动量✗）
  · 反转 corr（pwsh-301）：SOL −0.1507 ✓；SOL 在旧宇宙实测 maker 腿 +2.84bp（21 腿）
  · 步长表 SYMBOL_STEP 已有 SOL（step 0.01 / min_qty 0.01 / min_notional 5）✓

# 留观

  · AVAX / AAVE 换入后 ~25 分钟 0 腿，若 pwsh-302 观测 60 分钟仍 0 腿，
    按同法换 DOGE（−0.154，864/h）/ BNB（−0.164，425/h）。
  · 教训（记入选择器待办）：选币必须同时过滤「trades/h ≥ 400」，
    单看 corr 会选进 PENDLE 这种 book 活跃但无成交的币。

# 用法

    python scripts/h274_swap_pendle_sol.py            # 换币 + 验证热采用
    python scripts/h274_swap_pendle_sol.py --rollback
    python scripts/h274_swap_pendle_sol.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h274_swap_pendle_sol_state.json"
TARGET = ["SOL", "AVAX", "ETH", "AAVE"]
NOTE = ("H274 P1b：PENDLE 2h 0 成交（trades<0.1/s）→ SOL（corr −0.151、890 trades/h、"
        "旧宇宙实测 maker 腿 +2.84bp）。宇宙=[SOL, AVAX, ETH, AAVE]；"
        "AVAX/AAVE 留观，60 分钟仍 0 腿则换 DOGE/BNB。")


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
    print("H274  P1b 换币：PENDLE（0 成交）→ SOL")
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
        "reason": "PENDLE 2h 0 成交 → SOL（有流 + corr −0.151 + 实测 maker 腿 +2.84bp）",
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
