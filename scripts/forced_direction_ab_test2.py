# -*- coding: utf-8 -*-
"""受控行为验证 v2：用系统自带的 `refresh_thesis(force=True)` 对关键标的做一次重析。

## 为什么第一版失败（有价值的发现）
v1 想用 `MIDLONG_THESIS_TTL_MID_S=600` 触发，结果子进程 `watch=0 idle=0` **一条都没析**。
原因：批次里的 `stale` 判据读的是**库里持久化的 `expires_at`**（`thesis_is_fresh(latest)`），
**不是**重新计算的 TTL ⇒ 环境变量覆盖**无法**迫使已新鲜的论题重析。
真正可用的强制入口是 `brain.refresh_thesis(force=True)`（系统自己的参数，`:1352/:1367`）。

## 边界（据实声明）
- 直接调用**生产函数**（不是重写逻辑），`force=True` 只绕开"新鲜度"这一道**排程**门；
- 每个标的 1 次 LLM 调用，会**写一张新论题**（与调度器到期时做的事完全相同）；
- **不开仓**（本路径只分析写论题；开仓在 API 进程且另有多道闸）。
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGETS = [("VIRTUAL", "mid"), ("ASTER", "mid")]
TIER = "mid"

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal, SessionLocal  # noqa: E402


def latest_dirs():
    db = AnalyticsSessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text("""
            SELECT DISTINCT ON (symbol)
                   symbol, direction, llm_conviction, accepted, recommend_open, updated_at
            FROM mlto_thesis
            WHERE tier = 'mid'
            ORDER BY symbol, updated_at DESC NULLS LAST
        """)).fetchall()
        return {r[0]: r for r in rows}
    finally:
        db.close()


print("=== 重析前：每标的最新 mid 论题 ===")
before = latest_dirs()
for s, _t in TARGETS:
    r = before.get(s)
    print(f"   {s:10s} " + (f"dir={r[1]} conv={r[2]} accepted={r[3]} rec_open={r[4]} at={r[5]}"
                           if r else "（无）"))

# 取运行中的会话
db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    row = db.execute(text(
        "SELECT session_id FROM full_auto_sessions WHERE status='running' "
        "ORDER BY id DESC LIMIT 1")).fetchone()
    sid = str(row[0]) if row else ""
finally:
    db.close()
print(f"\n运行中会话 = {sid!r}")
if not sid:
    print("无运行会话 ⇒ 退出（不硬编 session_id）")
    raise SystemExit(0)

from backend.services.mlto import brain as B  # noqa: E402

print("\n=== 受控重析（refresh_thesis(force=True)）===")
t0 = time.time()
for sym, tier in TARGETS:
    try:
        dto = B.refresh_thesis(session_id=sid, symbol=sym, tier=tier, force=True)
        print(f"   {sym:10s} → dir={getattr(dto, 'direction', None)} "
              f"conv={getattr(dto, 'llm_conviction', None)} "
              f"accepted={getattr(dto, 'accepted', None)} "
              f"rec_open={getattr(dto, 'recommend_open', None)} "
              f"（{time.time()-t0:.0f}s）")
    except Exception as exc:  # noqa: BLE001
        print(f"   {sym:10s} 失败: {type(exc).__name__}: {str(exc)[:140]}")

print("\n=== 重析后 ===")
after = latest_dirs()
print(f"   {'symbol':10s} {'前':>9s} {'后':>9s}  变化")
for s, _t in TARGETS:
    b = before.get(s)
    a = after.get(s)
    bd = b[1] if b else "?"
    ad = a[1] if a else "?"
    print(f"   {s:10s} {str(bd):>9s} {str(ad):>9s}  "
          f"{'← 改变' if bd != ad else '（未变）'}"
          + (f"   [新行 conv={a[2]} accepted={a[3]} rec_open={a[4]} at={a[5]}]" if a else ""))
