# -*- coding: utf-8 -*-
"""直接验证：生产环境的 LLM prompt 里是否已注入【风控硬约束】（只读）。

比"看 LLM 行为有没有变"更直接：行为要样本、要时间；而 prompt 是**确定性的产物**。
来源：ai_decision_logs.prompt_snapshot（若写入方有落盘）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

db = AnalyticsSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text("""
        SELECT id, created_at, symbol, length(prompt_snapshot)
        FROM ai_decision_logs
        WHERE prompt_snapshot IS NOT NULL AND prompt_snapshot <> ''
          AND created_at >= now() - interval '40 minutes'
        ORDER BY id DESC LIMIT 20
    """)).fetchall()
    print(f"近 40 分钟有 prompt_snapshot 的记录 = {len(rows)}")
    for r in rows[:8]:
        print("   ", tuple(r))
    if not rows:
        rows = db.execute(text("""
            SELECT id, created_at, symbol, length(prompt_snapshot)
            FROM ai_decision_logs
            WHERE prompt_snapshot IS NOT NULL AND prompt_snapshot <> ''
            ORDER BY id DESC LIMIT 8
        """)).fetchall()
        print(f"（近 40 分钟无；全表最近 {len(rows)} 条）")
        for r in rows:
            print("   ", tuple(r))

    print("\n=== 统计：prompt_snapshot 里含哪一版风控文案 ===")
    for pat, label in (("风控硬约束", "新版（direction 必须写 neutral）"),
                       ("风控提示", "旧版（direction 可写 bearish）"),
                       ("空头开仓会被 regime 闸全部拦截", "共同片段")):
        n = db.execute(text(
            "SELECT COUNT(*) FROM ai_decision_logs WHERE prompt_snapshot LIKE :p"),
            {"p": f"%{pat}%"}).fetchone()[0]
        n_recent = db.execute(text(
            "SELECT COUNT(*) FROM ai_decision_logs "
            "WHERE prompt_snapshot LIKE :p AND created_at >= now() - interval '40 minutes'"),
            {"p": f"%{pat}%"}).fetchone()[0]
        print(f"   {label:34s} 全表={n:6d}  近40分钟={n_recent}")

    print("\n=== 最近一条含风控文案的 prompt 片段 ===")
    row = db.execute(text("""
        SELECT id, created_at, symbol, prompt_snapshot FROM ai_decision_logs
        WHERE prompt_snapshot LIKE '%风控%'
        ORDER BY id DESC LIMIT 1
    """)).fetchone()
    if row:
        snap = str(row[3])
        i = snap.find("【风控")
        print(f"   id={row[0]} ts={row[1]} symbol={row[2]}")
        print("   " + snap[i:i + 400].replace("\n", " ") if i >= 0 else "   （未找到标记）")
    else:
        print("   （无）")
finally:
    db.close()
