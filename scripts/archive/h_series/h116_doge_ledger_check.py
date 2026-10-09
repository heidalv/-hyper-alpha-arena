"""H116：收缩后 DOGE 成交的**账本核对** —— 判断谁写的这些成交。

# 背景

收缩（宇宙→[ASTER,SOL,XRP]）在 09:11 落地，但 fill_basis 里 DOGE 在
09:11:11 ~ 09:16:28 有 **18 笔**，且大部分是 **maker（fee=0.0）**——
即「正常做市」，而不是 plan_orphan_exit 的「单笔 taker 全平」。

当前运行态却是干净的（states 只有 3 币、orphan_inventory 为空）。

⇒ 必须回答：这 18 笔是**谁**写的？两种可能：
  a) 旧 worker 进程在收缩后仍在跑（杀掉时机/锁的竞态）⇒ 真实成交，账本应有
  b) 只有文件被写，账本没有 ⇒ 落账路径异常

# 判据

  · 账本（paper_pnl / ledger）里能找到 09:11~09:17 的 DOGE 行 ⇒ 假设 a ⇒ **真实敞口**
  · 找不到 ⇒ 假设 b ⇒ 文件/落账不一致，更严重

用法：
    .venv\\Scripts\\python.exe scripts\\h116_doge_ledger_check.py
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for junk in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(junk, "")
    return url


def main():
    url = dsn()
    print("=" * 100)
    print("H116  收缩后 DOGE 成交的账本核对")
    print("=" * 100)

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            # 找所有可能的成交/盈亏表
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public'
                  AND (table_name LIKE '%%pnl%%' OR table_name LIKE '%%fill%%'
                       OR table_name LIKE '%%ledger%%' OR table_name LIKE '%%trade%%'
                       OR table_name LIKE '%%position%%')
                ORDER BY table_name
            """)
            tabs = [r[0] for r in cur.fetchall()]
            print(f"\n候选表：{tabs}")

            for t in tabs:
                cur.execute("""
                    SELECT column_name, data_type FROM information_schema.columns
                    WHERE table_name=%s ORDER BY ordinal_position
                """, (t,))
                cols = cur.fetchall()
                names = [c[0] for c in cols]
                tcol = next((c for c in names
                             if c in ("ts", "event_ts", "created_at", "ts_ms",
                                      "event_ts_ms", "time", "ts_utc", "occurred_at")), None)
                scol = next((c for c in names if c == "symbol"), None)
                if not tcol or not scol:
                    print(f"\n  [{t}] 无 symbol/time 列，跳过  cols={names[:12]}")
                    continue
                # 该表的 symbol 命名口径
                try:
                    cur.execute(f'SELECT DISTINCT symbol FROM "{t}" LIMIT 12')
                    syms = [r[0] for r in cur.fetchall()]
                except Exception as e:
                    conn.rollback()
                    print(f"\n  [{t}] 读 symbol 失败：{e}")
                    continue
                tsc = tcol if "_ms" in tcol else tcol
                print(f"\n{'─'*100}\n  [{t}]  time列={tcol}  symbols={syms}")

                # 统一按「毫秒或秒」判断
                for sym in ("DOGE", "DOGEUSDT"):
                    if sym not in syms:
                        continue
                    try:
                        cur.execute(f'''
                            SELECT * FROM "{t}"
                            WHERE symbol=%s
                            ORDER BY "{tsc}" DESC LIMIT 25
                        ''', (sym,))
                        rs = cur.fetchall()
                        cnames = [d[0] for d in cur.description]
                        print(f"    {sym} 最近 {len(rs)} 行：")
                        for r in rs:
                            d = dict(zip(cnames, r))
                            keep = {k: v for k, v in d.items()
                                    if k in ("ts", "event_ts", "created_at", "ts_ms",
                                             "symbol", "side", "qty", "price", "fill_px",
                                             "pnl", "fee", "flatten", "lane_id", "leg",
                                             "reason", "realized_pnl")}
                            print(f"      {keep}")
                    except Exception as e:
                        conn.rollback()
                        print(f"    {sym} 查询失败：{e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
