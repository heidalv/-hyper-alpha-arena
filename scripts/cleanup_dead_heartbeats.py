# -*- coding: utf-8 -*-
"""D-3 死池清理（默认只报告；`--apply` 才删除）。

## 判定口径（必须写明）
"死池" = `kline_sync_heartbeat` 里 **`updated_at` 距今超过阈值** 的 (exchange, period, pool) 行。
- 阈值默认 **7 天**（`--days` 可调）：这些行的写入方早已不再运行
  （实测：`aggregate/*` 44 天、`asterdex p0/p1` 31–48 天）。
- 它们是**纯遥测行**（不含行情数据），删除只影响"池健康度"的读数清晰度；
  若对应采集器复活，下一轮会**自动重建**该行。
- **不删**：任何近 7 天内更新过的行（含当前活跃的 binance p0/p1/p2、各所 p2_depth）。

用法：
    python scripts/cleanup_dead_heartbeats.py            # 只报告
    python scripts/cleanup_dead_heartbeats.py --apply    # 删除
    python scripts/cleanup_dead_heartbeats.py --days 3   # 改阈值
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APPLY = "--apply" in sys.argv
DAYS = 7.0
if "--days" in sys.argv:
    try:
        DAYS = float(sys.argv[sys.argv.index("--days") + 1])
    except Exception:  # noqa: BLE001
        pass

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

db = MarketSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text("""
        SELECT exchange, period, pool, symbols_ok, symbols_fail,
               updated_at, last_attempt_at, last_success_at, consecutive_fail_rounds,
               EXTRACT(EPOCH FROM (now() - updated_at)) / 86400.0 AS age_days
        FROM kline_sync_heartbeat
        ORDER BY updated_at ASC
    """)).fetchall()
    print("=" * 104)
    print(f"心跳行共 {len(rows)} 条（阈值 {DAYS:g} 天；{'**APPLY 删除**' if APPLY else '只报告'}）")
    print("=" * 104)
    print(f"{'exchange':12s} {'period':6s} {'pool':10s} {'ok':>5s} {'fail':>6s} "
          f"{'年龄(天)':>9s} {'连败':>5s}  判定")
    dead = []
    for ex, per, pool, ok, fail, upd, att, suc, streak, age in rows:
        age_f = float(age or 0)
        is_dead = age_f > DAYS
        if is_dead:
            dead.append((ex, per, pool, age_f))
        print(f"{str(ex):12s} {str(per):6s} {str(pool):10s} {ok:5d} {fail:6d} "
              f"{age_f:9.2f} {int(streak or 0):5d}  {'☠ 死池' if is_dead else '活跃'}")

    print("-" * 104)
    print(f"死池（>{DAYS:g} 天未更新）= {len(dead)} 条：")
    for ex, per, pool, age in dead:
        print(f"   {ex:12s} {per:6s} {pool:10s} {age:.1f} 天")

    if APPLY and dead:
        n = db.execute(text("""
            DELETE FROM kline_sync_heartbeat
            WHERE updated_at < now() - make_interval(days => :d)
        """), {"d": int(DAYS)}).rowcount
        db.commit()
        print(f"\n✅ 已删除 {n} 条死池行（对应采集器若复活会自动重建）")
    elif APPLY:
        print("\n（无需删除）")
    else:
        print("\n提示：加 --apply 才真正删除。")
finally:
    db.close()
