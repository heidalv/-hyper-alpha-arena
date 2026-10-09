# -*- coding: utf-8 -*-
"""H193 换宇宙 —— 带回滚保护，且**拒绝在有持仓时执行**。

# 为什么必须换（2026-09-21 实测，这是"赚 0.00 几、亏就是几刀"的算术根源）

车道跑的是 ASTER/XRP/SOL，而它们的**实盘点差**：

    ASTER   1.33 bp
    SOL     1.72 bp
    XRP     2.02 bp

而一次完整往返的成本（taker 出库）是 **8 bp**（4bp 费率 + 约 4bp 穿价）。
每次 maker 捕获只有约 0.25 bp ⇒ **要 20 次 maker 才补回一次 taker 平仓**。
在 1.3~2.0bp 的点差上，这个账**在数学上永远补不回来**。

同期，选币器（同一个硬闸 + 机械评分）选出的币：

    VIRTUAL 20.27 bp    SEI 18.88    PENDLE 15.81    ONDO 11.35    ARB 10.13

⇒ 它们**能覆盖** 8bp 成本。深度采集也已覆盖这 28 个币（含上述 5 个）。

# 为什么"换宇宙"是危险操作

`meta.symbols` 一变，`_maybe_reload_meta`（F283）会热更新宇宙，
**被移除币的持仓由 `orphan_states()`（F90）强制退出** —— 那是 **taker** 平仓，
每次约 −$0.36（实测 257 笔强平均值）。

⇒ 本工具**硬拒绝**在有持仓时换宇宙（除非显式 `--force`，且会写明代价）。
正确做法是等持仓自然出库（maker 免费），再换。

# 用法

    python scripts/h193_swap_universe.py --show
    python scripts/h193_swap_universe.py --to VIRTUAL,SEI,PENDLE --why "点差 15-20bp 覆盖 8bp 成本"
    python scripts/h193_swap_universe.py --rollback
    python scripts/h193_swap_universe.py --status
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
STATE = ROOT / "logs" / "universe_swap_state.json"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def read_meta() -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            r = cur.fetchone()
    return dict(r[0] or {}) if r else {}


def write_symbols(symbols: list, *, note: str = "") -> None:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            meta["symbols"] = list(symbols)
            u = dict(meta.get("universe") or {})
            u["ai"] = list(symbols)
            if note:
                u["note"] = note
            u["as_of"] = datetime.now().astimezone().isoformat()
            meta["universe"] = u
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), LANE))
        c.commit()


def heartbeat() -> dict:
    try:
        return json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def open_positions() -> dict:
    """心跳里 qty 非零的币。"""
    hb = heartbeat()
    out = {}
    for sym, st in (hb.get("states") or {}).items():
        try:
            q = float((st or {}).get("qty") or 0.0)
        except Exception:
            q = 0.0
        if abs(q) > 1e-9:
            out[sym] = q
    return out


def live_symbols() -> list:
    return list(heartbeat().get("symbols") or [])


def wait_effective(expect: list, timeout_s: float = 150.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if sorted(live_symbols()) == sorted(expect):
            return True
        time.sleep(5)
    return False


def cmd_show() -> int:
    meta = read_meta()
    hb = heartbeat()
    print(f"  注册表 meta.symbols = {meta.get('symbols')}")
    print(f"  实盘心跳 symbols    = {hb.get('symbols')}")
    pos = open_positions()
    print(f"  当前持仓            = {pos if pos else '（空仓）'}")
    print(f"  心跳 ok={hb.get('ok')} ticks={hb.get('ticks')} equity={hb.get('equity')}")
    return 0


def cmd_swap(to: list, why: str, force: bool) -> int:
    if not to:
        print("  ✗ --to 不能为空")
        return 1
    to = [s.strip().upper() for s in to if s.strip()]

    before = list(read_meta().get("symbols") or [])
    pos = open_positions()

    print(f"  当前宇宙: {before}")
    print(f"  目标宇宙: {to}")
    print(f"  当前持仓: {pos if pos else '（空仓）'}")

    removed = [s for s in before if s not in to]
    if removed and pos:
        held = [s for s in removed if s in pos]
        if held and not force:
            print()
            print(f"  ✗ **拒绝执行**：被移除的 {held} 上还有持仓。")
            print(f"    换宇宙会让它们走 `orphan_states()` 强制 taker 平仓，")
            print(f"    实测每次约 −$0.36（257 笔强平均值）。")
            print(f"    ⇒ 等它们自然出库（maker 免费）后再换，或加 --force 明确承担代价。")
            return 1
        if held:
            print(f"  ⚠️ --force：{held} 的持仓将被 taker 强平（约 −$0.36/币）")

    if STATE.exists():
        old = json.loads(STATE.read_text(encoding="utf-8"))
        print(f"  ⚠️ 已有未完成的换宇宙记录（{old.get('saved_at')}）⇒ 先 --rollback 或确认")
        return 1

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "saved_at": datetime.now().astimezone().isoformat(),
        "lane": LANE, "original": before, "target": to, "why": why,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已保存原宇宙到 {STATE}")

    write_symbols(to, note=why or "H193 换宇宙")
    print(f"  已写入注册表，等待热采用（F283，≤60s）…")
    if not wait_effective(to):
        print(f"  ✗ 150s 内未生效（实盘 = {live_symbols()}）⇒ **自动回滚**")
        write_symbols(before, note="H193 回滚（热采用失败）")
        wait_effective(before, timeout_s=90.0)
        print(f"  已回滚，实盘 = {live_symbols()}")
        return 1

    STATE.unlink(missing_ok=True)
    print(f"  ✓ 热采用确认：实盘宇宙 = {live_symbols()}")
    print(f"  ⇒ 下一步：盯 `scripts/h191_exit_cost_board.py` 的 穿价% 与 flat$")
    return 0


def cmd_rollback() -> int:
    if not STATE.exists():
        print("  ✓ 无未完成的换宇宙记录")
        return 0
    st = json.loads(STATE.read_text(encoding="utf-8"))
    orig = list(st.get("original") or [])
    pos = open_positions()
    print(f"  回滚到: {orig}   当前持仓: {pos if pos else '（空仓）'}")
    write_symbols(orig, note="H193 回滚")
    if wait_effective(orig, timeout_s=150.0):
        STATE.unlink(missing_ok=True)
        print(f"  ✓ 已回滚，实盘宇宙 = {live_symbols()}")
        return 0
    print(f"  ⚠️ 回滚未确认；实盘 = {live_symbols()}（状态文件保留）")
    return 1


def cmd_status() -> int:
    if not STATE.exists():
        print("  ✓ 无未完成的换宇宙记录")
        return 0
    st = json.loads(STATE.read_text(encoding="utf-8"))
    print(f"  ⚠️ 未完成的换宇宙：{st.get('saved_at')}")
    print(f"     原宇宙: {st.get('original')}")
    print(f"     目标  : {st.get('target')}")
    print(f"     原因  : {st.get('why')}")
    print(f"     当前实盘: {live_symbols()}")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--to", default="")
    ap.add_argument("--why", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    print("=" * 92)
    print("H193  换宇宙（带回滚；有持仓时拒绝）")
    print("=" * 92)

    if a.rollback:
        return cmd_rollback()
    if a.to:
        return cmd_swap([x for x in a.to.split(",") if x.strip()], a.why, a.force)
    if a.status:
        return cmd_status()
    return cmd_show()


if __name__ == "__main__":
    raise SystemExit(main())
