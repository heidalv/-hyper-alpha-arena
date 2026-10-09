"""诊断：AI 选币到底在不在跑？有没有更新过？

用户问：「ai选币好像没有更新过，是在运行么？」

本脚本查清四件事：
  1. **HFT 车道的选币**（`coin_select_hft.select_universe`）现在选出来什么、as_of 是几点
  2. **平台选币调度器**（`coin_select_platform_service`）的开关与最近一次扫描
  3. **数据库表**：`coin_select_scans` / `coin_select_candidates` / `coin_select_adoptions`
     各自的最新时间戳与行数（"没更新"最直接的证据）
  4. **管理员 coin_select LLM** 是否就绪（没 Key 就只会退回规则分，不调用 AI）

用法：
    .venv\\Scripts\\python.exe scripts\diag_ai_coin_select.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import time

    print("=" * 100)
    print("AI 选币运行状态诊断")
    print("=" * 100)

    # ── 1) HFT 车道选币 ──
    print("\n【1】HFT 车道选币（coin_select_hft.select_universe）")
    print("-" * 100)
    try:
        from backend.services import lane_registry as reg
        from backend.services.coin_select_hft import select_universe
        lane = reg.get_lane(os.getenv("MM_LANE_ID", "mm_asterdex")) or {}
        meta = lane.get("meta") or {}
        fixed = (meta.get("universe") or {}).get("fixed") or []
        print(f"  车道 meta.universe.fixed = {fixed}")
        print(f"  车道当前 symbols       = {meta.get('symbols')}")
        for slots in (0, 3, 5):
            t0 = time.time()
            d = select_universe(fixed=fixed, ai_slots=slots)
            dt = (time.time() - t0) * 1000
            picked = d.get("symbols") if isinstance(d, dict) else d
            print(f"\n  ai_slots={slots}  ({dt:.0f}ms)")
            print(f"    选中: {picked}")
            if isinstance(d, dict):
                for k in ("as_of", "ai_symbols", "fixed_symbols", "source", "reason",
                          "degraded", "scores"):
                    v = d.get(k)
                    if v is not None:
                        s = str(v)
                        print(f"    {k}: {s[:220]}")
    except Exception as e:
        import traceback
        print(f"  ✗ 失败: {type(e).__name__}: {e}")
        traceback.print_exc()

    # ── 2) 平台调度器 ──
    print("\n【2】平台选币调度器")
    print("-" * 100)
    try:
        from backend.config import settings as S
        print(f"  COIN_SELECT_PLATFORM_SCHEDULER_ENABLED = "
              f"{getattr(S, 'COIN_SELECT_PLATFORM_SCHEDULER_ENABLED', '(无)')}")
        from backend.services.coin_select_platform_service import (
            coin_select_platform_scheduler as sch)
        for attr in ("enabled", "is_running", "_task", "interval", "last_run_ts",
                     "last_run", "next_run"):
            if hasattr(sch, attr):
                print(f"  scheduler.{attr} = {getattr(sch, attr)}")
        print(f"  scheduler 类型 = {type(sch).__name__}")
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")

    # ── 3) 数据库表的最新时间戳 ──
    print("\n【3】数据库表（'没更新'最直接的证据）")
    print("-" * 100)
    import psycopg2
    import psycopg2.extras
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    now = time.time()
    for t, tcol in (("coin_select_scans", None),
                    ("coin_select_candidates", None),
                    ("coin_select_adoptions", None),
                    ("auto_coin_selections", None)):
        try:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns"
                        " WHERE table_name=%s ORDER BY ordinal_position", (t,))
            cols = [r["column_name"] for r in cur.fetchall()]
            if not cols:
                print(f"\n  {t}: **表不存在**")
                continue
            timecols = [c for c in cols
                        if any(k in c.lower() for k in ("_at", "ts", "time", "date"))]
            cur.execute(f"SELECT count(*) n FROM {t}")
            n = cur.fetchone()["n"]
            print(f"\n  {t}:  {n:,} 行")
            print(f"    列: {cols[:14]}")
            for c in timecols[:4]:
                try:
                    cur.execute(f"SELECT max({c}) mx, min({c}) mn FROM {t}")
                    r = cur.fetchone()
                    age = ""
                    if r["mx"] is not None:
                        try:
                            import datetime
                            mx = r["mx"]
                            if isinstance(mx, datetime.datetime):
                                age_s = now - mx.timestamp()
                                age = f"  （距今 {age_s/3600:.1f} 小时）"
                        except Exception:
                            pass
                    print(f"    max({c}) = {r['mx']}{age}    min = {r['mn']}")
                except Exception as e:
                    print(f"    max({c}) 失败: {e}")
        except Exception as e:
            print(f"\n  {t}: 查询失败 {type(e).__name__}: {e}")
    cn.close()

    # ── 4) coin_select LLM ──
    print("\n【4】管理员 coin_select LLM 是否就绪")
    print("-" * 100)
    try:
        from backend.services.coin_select_platform_service import get_admin_coin_select_llm
        llm = get_admin_coin_select_llm()
        if llm is None:
            print("  ✗ **未就绪** ⇒ 选币只会退回**规则分**，不调用 AI")
        else:
            print(f"  ✓ 就绪: {llm}")
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")

    print("\n" + "=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
