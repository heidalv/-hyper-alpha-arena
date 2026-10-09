# -*- coding: utf-8 -*-
"""[F362 2026-09-18] v7 教训池的**可达性**实测：Codegen 侧还剩多少"写了永远读不到"。

## 为什么查这个

本次复查的核心判据是「写必有读」。交易侧已由 `learning_readback` 修好（§17），
但 **Codegen 侧**（因子挖掘 prompt）仍是旧实现：

    evolution_memory_v7.build_codegen_context():
        SELECT ... WHERE status='active' AND (period=? OR cycle=? OR cycle='X')
        ORDER BY id DESC LIMIT 200        ← 先按 id 截断到 200 条，再打分取 8 条

⇒ 同一 (period, cycle) 下，**id 更老的第 201 条起永远不可能被检索到**，与它质量多高无关。
本脚本把"永远读不到"的条数算出来（离线、只读），作为「学习只写不读」的**最后一块读数**。

## 口径

- **可达集** = 对每个真实出现过的 (period, cycle) 组合，取 `id DESC LIMIT 200` 的并集；
  再与 `status='active'` 求交。
- **不可达条数** = active 总数 − 可达数。
- 另报"可达但从未被检索过"（`use_count=0`）——它区分"能读"与"读到了"。
"""
from __future__ import annotations

import io
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB = ROOT / "backend" / "data" / "factor_evolution_memory_v7.db"
LIMIT = 200          # 与 build_codegen_context 的 LIMIT 保持一致


def main() -> int:
    if not DB.exists():
        print("找不到 v7 库:", DB)
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    print("=" * 78)
    print("F362 v7 教训池可达性（Codegen 侧 / 离线只读）")
    print("=" * 78)

    pairs = cur.execute(
        "SELECT DISTINCT period, cycle FROM v7_lessons WHERE status='active'").fetchall()
    print(f"\n① 真实出现过的 (period, cycle) 组合：{len(pairs)} 个")
    print("   " + ", ".join(f"{p}/{c}" for p, c in sorted(pairs, key=str)))

    reachable: set = set()
    per_pair = []
    for period, cycle in pairs:
        ids = [r[0] for r in cur.execute(
            "SELECT id FROM v7_lessons WHERE status='active' AND (period=? OR cycle=?) "
            "ORDER BY id DESC LIMIT ?", (period, cycle, LIMIT)).fetchall()]
        per_pair.append((period, cycle, len(ids)))
        reachable.update(ids)
    print(f"\n② 每个组合的候选窗口（命中行数，上限 {LIMIT}）")
    for period, cycle, n in sorted(per_pair, key=lambda x: -x[2]):
        flag = "  ← 被窗口截断" if n >= LIMIT else ""
        print(f"   {str(period):<6}/{str(cycle):<3} {n:>5}{flag}")

    active_total = cur.execute(
        "SELECT COUNT(*) FROM v7_lessons WHERE status='active'").fetchone()[0]
    active_ids = {r[0] for r in cur.execute(
        "SELECT id FROM v7_lessons WHERE status='active'").fetchall()}
    reach = reachable & active_ids
    un = active_ids - reach
    print(f"\n③ 可达性")
    print(f"   active 总数        = {active_total}")
    print(f"   可达（并集∩active）= {len(reach)}")
    print(f"   **不可达**         = {len(un)}  （{len(un) / max(active_total, 1):.1%}）")

    if un:
        print("\n④ 不可达条目的构成（这些教训无论多高质量都不可能进入 Codegen prompt）")
        ph = ",".join("?" * len(un))
        for k, n in cur.execute(
                f"SELECT kind, COUNT(*) FROM v7_lessons WHERE id IN ({ph}) "
                f"GROUP BY kind ORDER BY 2 DESC", tuple(un)).fetchall():
            print(f"     {k:<16}{n}")
        print("   样例（id 最小=最早写入的）：")
        for r in cur.execute(
                f"SELECT id, kind, period, cycle, substr(title,1,60) FROM v7_lessons "
                f"WHERE id IN ({ph}) ORDER BY id ASC LIMIT 6", tuple(un)).fetchall():
            print(f"     #{r[0]:<5}{r[1]:<16}{r[2]}/{r[3]}  {r[4]}")

    print("\n⑤ 「能读」vs「读到了」")
    used_reach = cur.execute(
        "SELECT COUNT(*) FROM v7_lessons WHERE status='active' AND use_count>0").fetchone()[0]
    print(f"   active 中被检索过（use_count>0）= {used_reach}（{used_reach / max(active_total, 1):.1%}）")
    print(f"   ⇒ 即使进入 200 窗口，每次调用也只取 **8 条**；池子增长远快于检索覆盖。")

    print("\n⑥ 对照：读回路（learning_readback）的口径")
    try:
        from backend.services.evolution.evolution_memory_v7 import build_codegen_context
        src = __import__("inspect").getsource(build_codegen_context)
        print(f"   build_codegen_context 仍含 `ORDER BY id DESC LIMIT 200`："
              f"{'是（默认路径）' if 'LIMIT 200' in src else '否'}")
    except Exception as exc:
        print("   无法读取源码:", str(exc)[:120])
    print("   读回路（F345）**没有** id 窗口：全量 active 参与打分 ⇒ 交易侧无此盲区。")
    con.close()

    # ── ⑦ 开关前后对照（跑在**库副本**上，绝不改生产库） ──
    print("\n⑦ 开关对照（V7_CODEGEN_FULL_POOL 关/开；跑在库副本上）")
    import os
    import shutil
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="v7r_"))
    copy = tmpdir / "v7.db"
    shutil.copy2(DB, copy)
    before_cnt = None
    try:
        con2 = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        before_cnt = con2.execute(
            "SELECT COALESCE(SUM(use_count),0) FROM v7_lessons").fetchone()[0]
        con2.close()
    except Exception:
        pass
    os.environ["V7_MEMORY_DB_PATH"] = str(copy)
    print("   说明：**不做**『某条教训是否被放出来』的文本归属——池子里有数百条"
          "**逐字相同**的标题（`4h 进化链中断: no_survivors` 等），")
    print("         渲染文本不带 id，任何按标题匹配的归属都会把可达到的条目误算成不可达的。")
    print("         id 级证明在单测里：`test_full_pool_flag_makes_oldest_reachable`"
          "（最老且质量最高的那条只在开关打开时出现）。")
    print("         此处只报**可确证**的量：两种模式产出的行数 + 生产库未被改动。")
    try:
        from backend.services.evolution import evolution_memory_v7 as EV7
        for flag, label in (("0", "关（默认，旧行为）"), ("1", "开（全量打分）")):
            os.environ["V7_CODEGEN_FULL_POOL"] = flag
            EV7._DB_PATH = copy
            EV7._CODEGEN_SATURATED_WARNED.clear()
            lines_n = 0
            chars = 0
            for p in ("4h", "1d", "1h", "15m", "5m"):
                c = EV7.build_codegen_context(p, limit=8)
                for ln in c.splitlines():
                    if ln.startswith("- ["):
                        lines_n += 1
                chars += len(c)
            print(f"   {label}: 5 个周期共 {lines_n} 行教训，合计 {chars} 字符")
    except Exception as exc:
        print("   对照失败:", str(exc)[:200])
    finally:
        os.environ.pop("V7_MEMORY_DB_PATH", None)
        os.environ.pop("V7_CODEGEN_FULL_POOL", None)
    # 生产库未被改动
    try:
        con3 = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        after_cnt = con3.execute("SELECT COALESCE(SUM(use_count),0) FROM v7_lessons").fetchone()[0]
        con3.close()
        print(f"   生产库 use_count 合计：{before_cnt} → {after_cnt}"
              f"  ⇒ {'未改动' if before_cnt == after_cnt else '**被改动，需排查**'}")
    except Exception as exc:
        print("   生产库校验失败:", str(exc)[:120])
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
