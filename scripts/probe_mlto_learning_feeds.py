# -*- coding: utf-8 -*-
"""[F375 2026-09-18] MLTO 主脑的**四路学习饲料**现状实测（只读）。

复查已确认：MLTO 只读它自己那套学习（`reflexion_memory` / `similar_episodes` /
`backtest_wisdom` / `consolidated_lessons`），不读 v7 教训/因子权重/衰减（后者已由
`learning_readback` 补上，默认关）。但"**声明了这四路**"与"**这四路真有内容**"是两件事——
§15 已记录 `consolidated_lessons` 仅 **167 字符**。本脚本把四路逐一实测，回答：
**策略主脑调用学习时，到底拿得到多少东西？**

只读；不写库、不改状态。取一个真实 session/symbol（默认取最近有持仓的 session）。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _pick_context():
    """挑一个真实 (session_id, symbol, tier)：优先取最近有论文的 session。"""
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            row = db.execute(text(
                "SELECT session_id, symbol, tier FROM mlto_theses "
                "ORDER BY updated_at DESC NULLS LAST LIMIT 1")).fetchone()
        finally:
            db.close()
        if row:
            return str(row[0]), str(row[1]), str(row[2])
    except Exception as exc:
        print(f"  [DB] 取 session 失败: {str(exc)[:120]}")
    return "sess_probe", "BTC", "mid"


def _brief(v, n=220) -> str:
    if v is None:
        return "None"
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return f"{len(s)} 字符 | {s[:n]}"


def main() -> int:
    sid, sym, tier = _pick_context()
    print("=" * 92)
    print(f"MLTO 四路学习饲料实测  session={sid} symbol={sym} tier={tier}")
    print("=" * 92)

    import backend.services.mlto.brain as B

    rows = []
    # ① reflexion_memory
    try:
        v = B._reflexion_memory(sym)
        n = int((v or {}).get("n") or 0)
        rows.append(("reflexion_memory", n, _brief(v)))
    except Exception as exc:
        rows.append(("reflexion_memory", -1, f"异常 {str(exc)[:100]}"))
    # ② similar_episodes
    try:
        v = B._similar_episodes_feed(sym, tier, sid, {})
        n = int((v or {}).get("n") or 0) if isinstance(v, dict) else -1
        rows.append(("similar_episodes", n, _brief(v)))
    except Exception as exc:
        rows.append(("similar_episodes", -1, f"异常 {str(exc)[:100]}"))
    # ③ backtest_wisdom
    try:
        v = B._wisdom_feed(sid, tier)
        n = int((v or {}).get("n") or 0) if isinstance(v, dict) else -1
        rows.append(("backtest_wisdom", n, _brief(v)))
    except Exception as exc:
        rows.append(("backtest_wisdom", -1, f"异常 {str(exc)[:100]}"))
    # ④ consolidated_lessons
    try:
        v = B._consolidated_lessons_feed()
        rows.append(("consolidated_lessons", len(v or ""), _brief(v)))
    except Exception as exc:
        rows.append(("consolidated_lessons", -1, f"异常 {str(exc)[:100]}"))

    print(f"\n{'饲料':<22}{'条数/长度':>10}   样例")
    print("-" * 92)
    for name, n, brief in rows:
        print(f"{name:<22}{n:>10}   {brief}")

    empty = [r[0] for r in rows if r[1] == 0 or (isinstance(r[2], str) and r[2].startswith("None"))]
    print("\n" + "=" * 92)
    print("判定")
    print("=" * 92)
    if empty:
        print(f"  ⚠️ 空饲料：{empty}")
        print("     ⇒ 主脑『调用学习』时这几路**拿不到内容**（不是没接线，是上游没数据）")
    else:
        print("  ✅ 四路都有内容")
    print("  说明：本脚本只测**读侧**；某路为空时需回到写侧（产出该饲料的周期任务）查为何没有数据。")

    # 产出侧旁证：相关表的行数与最新时间
    print("\n" + "=" * 92)
    print("写侧旁证（相关表行数与最新写入）")
    print("=" * 92)
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SET app.is_admin='on'"))
            for tbl, tcol in (("mlto_theses", "updated_at"), ("brain_lessons", "created_at"),
                              ("reflexion_memories", "created_at"), ("consolidated_lessons", "created_at"),
                              ("market_episodes", "created_at"), ("insight_wisdom", "created_at")):
                try:
                    n = db.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
                    mx = db.execute(text(f"SELECT MAX({tcol}) FROM {tbl}")).scalar()
                    print(f"  {tbl:<24}{n:>8} 行   最新 {tcol}={mx}")
                except Exception as exc:
                    print(f"  {tbl:<24} 查询失败: {str(exc)[:70]}")
        finally:
            db.close()
    except Exception as exc:
        print("  DB 旁证失败:", str(exc)[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
