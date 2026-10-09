"""H118：从 lane_ledger 的写入顺序判定「单一写入者还是并发写入者」。

# 思路

`lane_ledger.id` 是自增主键。若只有一个进程写账本，那么按 id 排序时，
`ts` 必须**单调不减**。若存在第二个写入者，必然出现 **id 顺序与 ts 顺序矛盾**
（后写的 id 带着更早的 ts，或两个进程的 ts 交错跳变）。

这比在代码里猜「哪条路径能报出非宇宙币」直接得多。

# 判据（事先定死）

  · id 升序时 ts 单调不减 ⇒ **单一写入者** ⇒ 非宇宙币成交来自**报价生成逻辑**的漏洞
  · 出现 ts 回退（倒挂）⇒ **并发写入者** ⇒ 收缩前必杀干净旧进程

用法：
    .venv\\Scripts\\python.exe scripts\\h118_ledger_ordering.py
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-09-21 09:05:00+08"
T1 = "2026-09-21 09:35:00+08"


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


def main():
    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, ts, symbol, event,
                       meta_json->>'side'      AS side,
                       meta_json->>'qty'       AS qty,
                       meta_json->>'source'    AS source,
                       meta_json->>'flatten'   AS flatten,
                       position_id, position_id_state
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND ts >= %s AND ts <= %s
                ORDER BY id
            """, (T0, T1))
            rows = cur.fetchall()

    print("=" * 100)
    print("H118  lane_ledger 写入顺序（id 升序）")
    print("=" * 100)
    print(f"  窗口 {T0} ~ {T1}   行数 {len(rows)}")
    print(f"\n  {'id':>6} {'ts':<20} {'币':<8} {'事件':<10} {'side':<5} "
          f"{'qty':>13} {'flat':<5} {'pid_state':<10}")
    print("  " + "-" * 92)
    prev_ts = None
    inversions = []
    for (rid, ts, sym, ev, side, qty, source, flat, pid, pstate) in rows:
        flag = ""
        if prev_ts is not None and ts < prev_ts:
            flag = "  <<< ts 倒挂"
            inversions.append((rid, ts, sym))
        prev_ts = ts
        q = float(qty) if qty else 0.0
        print(f"  {rid:>6} {ts.strftime('%H:%M:%S.%f')[:15]:<20} {str(sym):<8} "
              f"{str(ev):<10} {str(side):<5} {q:>13.4f} {str(flat):<5} "
              f"{str(pstate):<10}{flag}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    print(f"  id 与 ts 的顺序倒挂次数：**{len(inversions)}**")
    if inversions:
        print("  ⇒ **存在并发写入者**（两个进程同时写同一车道账本）")
        for rid, ts, sym in inversions[:10]:
            print(f"     id={rid} ts={ts.strftime('%H:%M:%S')} sym={sym}")
    else:
        print("  ⇒ **单一写入者**（id 升序时 ts 单调不减）")
        print("     ⇒ 非宇宙币的做市成交**不是**第二个进程写的，")
        print("        而是**报价生成逻辑本身**能把宇宙外的币报出去 ⇒ 引擎缺陷。")

    # 非宇宙币行
    uni = {"ASTER", "SOL", "XRP"}
    aliens = [r for r in rows if r[2] not in uni]
    print(f"\n  窗口内非宇宙币成交 {len(aliens)} 笔：")
    for r in aliens:
        print(f"     id={r[0]} {r[1].strftime('%H:%M:%S')} {r[2]} {r[4]} "
              f"qty={float(r[5] or 0):.2f} flat={r[7]} state={r[9]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
