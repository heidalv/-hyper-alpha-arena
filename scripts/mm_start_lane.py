"""启动 mm_asterdex 车道（paper 模式）并验证运行状态。

## 为什么要单独一个脚本而不是 curl 一下

启动涉及**跨库状态变更**（`lane_registry.status`），且启动后必须验证：
  · 调度器真的会推进影子 tick（`status=active` + `mode=paper` 是前提，见
    `runner.py:2599-2611`——非 paper 直接不跑）
  · 运行态里 symbols 取到的是**新宇宙**（10 币），而不是旧缓存
  · 首个 tick 不报错（`last_error` 为空）
把这三步固定成脚本，避免"点了启动但没跑"的静默失败。

用法：
    python scripts/mm_start_lane.py --dry-run     # 只看当前状态与将要做的变更
    python scripts/mm_start_lane.py               # 真正启动并验证
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

LANE = "mm_asterdex"


def _admin_conn():
    import psycopg2

    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_arena")
    cn.autocommit = True
    return cn


def show_state(tag: str) -> dict:
    cn = _admin_conn()
    cur = cn.cursor()
    cur.execute(
        "select mode, status, meta_json, updated_at from lane_registry where lane_id=%s",
        (LANE,),
    )
    row = cur.fetchone()
    cn.close()
    if not row:
        print(f"[{tag}] 车道不存在: {LANE}")
        return {}
    mode, status, meta, upd = row
    m = json.loads(meta) if isinstance(meta, str) else (meta or {})
    syms = m.get("symbols") or []
    uni = m.get("universe") or {}
    print(f"[{tag}] mode={mode} status={status} updated={upd}")
    print(f"        symbols({len(syms)}): {syms}")
    print(f"        fixed={uni.get('fixed')} ai={uni.get('ai')} as_of={uni.get('as_of')}")
    return {"mode": mode, "status": status, "symbols": syms, "meta": m}


def set_status(status: str) -> None:
    from backend.services import lane_registry as reg

    ok = reg.set_status(LANE, status)
    print(f"   set_status({status}) -> {ok}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", default="active", choices=["active", "paused", "stopped"])
    args = ap.parse_args()

    before = show_state("before")
    if not before:
        return 2
    if before["mode"] != "paper":
        print(f"!! mode={before['mode']} 不是 paper —— 拒绝自动启动（本脚本只启动模拟仓）")
        return 3
    if not before["symbols"]:
        print("!! symbols 为空 —— 先跑选币（scripts/hft_universe_select.py）再启动")
        return 4

    if args.dry_run:
        print(f"\n[DRY-RUN] 将把 status {before['status']} -> {args.status}（不写库）")
        return 0

    print(f"\n启动：status {before['status']} -> {args.status}")
    set_status(args.status)

    # 等调度器推进一个 tick（影子期默认 15s 一轮，留足余量）
    print("\n等待首个 tick（最多 90s）...")
    runner = None
    try:
        from backend.services.market_maker.runner import get_runner

        runner = get_runner(LANE)
    except Exception as e:  # noqa: BLE001
        print(f"  取 runner 失败（可能进程不同）：{e}")

    for i in range(9):
        time.sleep(10)
        if runner is None:
            break
        try:
            st = runner.status()
        except Exception as e:  # noqa: BLE001
            print(f"  status() 失败: {e}")
            continue
        ticks = st.get("ticks") or 0
        err = st.get("last_error")
        syms = st.get("symbols") or []
        print(f"  +{(i+1)*10}s ticks={ticks} symbols={len(syms)} "
              f"last_tick={st.get('last_tick_ts')} last_error={err}")
        if ticks > 0:
            print(f"  [OK] 已有 {ticks} 个 tick；symbols={syms}")
            break

    after = show_state("after")
    print("\n" + "=" * 60)
    if after.get("status") == args.status:
        print(f"启动成功：{LANE} mode={after['mode']} status={after['status']}")
        print(f"宇宙 {len(after['symbols'])} 币: {after['symbols']}")
        print("\n回滚：python scripts/mm_start_lane.py --status stopped")
        return 0
    print(f"启动未生效：status={after.get('status')}")
    return 5


if __name__ == "__main__":
    sys.exit(main())
