# -*- coding: utf-8 -*-
"""H270 P1 选币改造：ASTER（反转最弱）→ PENDLE（反转最强）。

# 验证依据（全部已跑完）

  H261 逐币反转（账本腿）：ASTER +2.51（四币最弱）< SOL +3.00 < HYPE +3.62 < XRP +4.03
  H262 候选池 corr：PENDLE −0.1357（全场最强，SOL 的 2 倍、HYPE 的 7 倍）
  H263 逆势差（纯 tick）：PENDLE +2.257bp（当前最强 XRP 的 2 倍）
  LLM 监控 30min：ASTER net −2.28bp 是当日亏损主因，SOL/HYPE 已转正

# 前提（已检查）

  · ASTER 无持仓 ✓（换币不会触发 orphan taker 平仓）
  · PENDLE 步长已补进 SYMBOL_STEP（step=1, minQty=1, minNotional=5，F344）
  · PENDLE 价格 ~2.42，单腿 $250 → 103 枚，合规 ✓
  · 选择器 DSH_HFT_UNIVERSE_SELECT 会每 30 分钟覆写宇宙 ⇒ 本实验需先禁用

# 回滚（修复 h193 的坑）

  h193 热采用成功后删 STATE 导致 --rollback 失效。本脚本**保留 STATE**，
  直到显式 --rollback 才删。

# 用法

    python scripts/h270_p1_swap_aster_pendle.py            # 换币 + 验证
    python scripts/h270_p1_swap_aster_pendle.py --rollback
    python scripts/h270_p1_swap_aster_pendle.py --status
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "h270_p1_swap_state.json"
TARGET = ["SOL", "XRP", "HYPE", "PENDLE"]


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


def write_symbols(syms: list, note: str = "") -> None:
    import psycopg
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
    print("H270  P1 选币改造：ASTER → PENDLE")
    print("=" * 96)

    if a.rollback:
        if not STATE.exists():
            print("  ✗ 无快照（可能已回滚）")
            return 1
        snap = json.loads(STATE.read_text(encoding="utf-8"))
        orig = snap["original"]
        write_symbols(orig, "P1 回滚")
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
    # 持仓检查：被移除的 ASTER 若有持仓则拒绝（用运行态 states，账本 SQL 有口径坑）
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    states = j.get("states") or {}
    for sym in ("ASTER",):
        q = float((states.get(sym) or {}).get("qty") or 0)
        if abs(q) > 1e-9:
            print(f"  ✗ 拒绝：ASTER 有持仓 {q}，会走 orphan taker 平仓")
            return 1
    STATE.write_text(json.dumps({
        "saved_at": dt.datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": before, "target": TARGET,
        "reason": "P1 选币：ASTER 反转最弱（+2.51bp、30min −2.28bp 亏损主因）→ PENDLE 反转最强（corr −0.1357、逆势差 +2.257bp）",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 快照存 {STATE}（保留，直到显式 --rollback）")
    write_symbols(TARGET, "P1 选币改造")
    print(f"  已写入，等热采用（≤60s）…")
    t0 = time.time()
    while time.time() - t0 < 150:
        live = live_symbols()
        if "PENDLE" in live and "ASTER" not in live:
            print(f"  ✓ 热采用确认（{time.time()-t0:.0f}s）：{live}")
            return 0
        time.sleep(5)
    print(f"  ⚠️ 150s 未确认生效（实盘 {live_symbols()}）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
