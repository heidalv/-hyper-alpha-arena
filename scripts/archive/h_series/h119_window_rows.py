"""H119：新 worker 运行窗口内（09:12:24 之后）**全部**账本行逐条列出。

# 为什么

H118 判定「单一写入者」，但 DOGE 在 09:13:59~09:16:28 仍有 maker 成交。
如果这段窗口内**只有 DOGE**、没有 ASTER/XRP/SOL，那说明是这个进程在
「宇宙=3 币」的同时给 DOGE 报价 —— 只可能来自孤儿路径或状态复活。

如果这段窗口内 ASTER/XRP/SOL 与 DOGE **交错**，那说明是**旧进程还在跑**。

用法：
    .venv\\Scripts\\python.exe scripts\\h119_window_rows.py
"""
from __future__ import annotations

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
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main():
    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, ts, symbol, event,
                       meta_json->>'side'    AS side,
                       meta_json->>'qty'     AS qty,
                       meta_json->>'flatten' AS flat,
                       meta_json->>'source'  AS source
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND ts >= '2026-09-21 09:12:20+08'
                ORDER BY id
            """)
            rows = cur.fetchall()

    print("=" * 100)
    print("H119  09:12:20 之后全部账本行")
    print("=" * 100)
    print(f"  行数 {len(rows)}\n")
    print(f"  {'id':>6} {'ts':<16} {'币':<8} {'side':<5} {'qty':>13} {'flat':<6} source")
    print("  " + "-" * 76)
    for (rid, ts, sym, ev, side, qty, flat, source) in rows:
        q = float(qty) if qty else 0.0
        print(f"  {rid:>6} {ts.strftime('%H:%M:%S.%f')[:12]:<16} {str(sym):<8} "
              f"{str(side):<5} {q:>13.4f} {str(flat):<6} {source}")

    # 每 tick 的币种组合
    print("\n" + "=" * 100)
    print("按 tick（同 ts）看币种组合")
    print("=" * 100)
    from collections import defaultdict
    byts = defaultdict(list)
    for r in rows:
        byts[r[1]].append(r[2])
    for ts in sorted(byts):
        syms = byts[ts]
        uni = [s for s in syms if s in ("ASTER", "SOL", "XRP")]
        ali = [s for s in syms if s not in ("ASTER", "SOL", "XRP")]
        tag = ""
        if ali and uni:
            tag = "  <= 宇宙内 + 宇宙外 交错"
        elif ali and not uni:
            tag = "  <<< 只有宇宙外"
        print(f"  {ts.strftime('%H:%M:%S')}  {','.join(sorted(syms)):<32}{tag}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    only_alien = [ts for ts in byts
                  if all(s not in ("ASTER", "SOL", "XRP") for s in byts[ts])]
    mixed = [ts for ts in byts
             if any(s not in ("ASTER", "SOL", "XRP") for s in byts[ts])
             and any(s in ("ASTER", "SOL", "XRP") for s in byts[ts])]
    print(f"  只有宇宙外币的 tick：{len(only_alien)}")
    print(f"  宇宙内外交错的 tick：{len(mixed)}")
    if mixed and not only_alien:
        print("  ⇒ 交错 ⇒ 像是**旧进程仍在跑**（它同时挂 3 币与 DOGE）")
    elif only_alien and not mixed:
        print("  ⇒ 只有宇宙外币 ⇒ **不是**旧进程（旧进程会同时挂 ASTER/XRP/SOL）")
        print("     ⇒ 只能是**孤兒路径或状态复活**把 DOGE 报了出去 ⇒ 引擎缺陷")
    else:
        print("  ⇒ 两种 tick 都有，需细查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
