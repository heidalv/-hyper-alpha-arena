# -*- coding: utf-8 -*-
"""[F346 探针 B] 中长线（唯一真正消费因子的车道）实际投票权重 vs 学习层归零裁定。

只读。判定链：
  factor_ic_evaluator.py:441-442  IC<=0 → weight=0.0（"不参与合成"，2026-08-31 L4 修复）
  → factor_ic_evaluator.py:95      max(0.1, min(2.0, v))  ← 0 被夹成 0.1
  → combo_weights.py:35-36         fid in manual ⇒ base[fid] = manual[fid]（覆盖）
  → combo_weights.py:72            归一化 v/tot ⇒ 被"排除"的因子仍拿到非零票权
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WEIGHTS = ROOT / "data" / "factor_runtime_weights.json"


def guc_admin() -> None:
    """RLS：alpha_arena 有 58 张 FORCE ROW LEVEL SECURITY 表，不设 GUC 会读到 0 行。"""
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(__import__("sqlalchemy").text("SET app.is_admin='on'"))
            db.commit()
        finally:
            db.close()
        print("  [GUC] app.is_admin='on' 已设置（本次会话）")
    except Exception as e:
        print("  [GUC] 设置失败:", str(e)[:160])


def main() -> int:
    print("=" * 78)
    print("F346 探针 B：中长线投票权重 vs 学习层归零裁定")
    print("=" * 78)
    w = json.loads(WEIGHTS.read_text(encoding="utf-8"))
    raw = {str(k): float(v or 0) for k, v in (w.get("weights") or {}).items()}
    zero = {k for k, v in raw.items() if v == 0.0}
    print(f"原始权重文件：条目={len(raw)} 零权重={len(zero)} updated_at={w.get('updated_at')}")

    print("\n① 取中长线活跃因子集（与实盘同一入口）")
    try:
        from backend.services.factor_engine.midlong_active_factor_set import (
            midlong_active_factor_set as M)
        active = M.get_active_factors() or []
    except Exception as e:
        print("  取活跃集失败:", str(e)[:300])
        return 1
    print(f"  active 因子数={len(active)}")
    if not active:
        print("  ⇒ 空集（可能 RLS 未放行）。尝试设置 GUC 后重取。")
        guc_admin()
        try:
            active = M.get_active_factors() or []
        except Exception as e:
            print("  重取失败:", str(e)[:200])
        print(f"  重取后 active 因子数={len(active)}")

    print("\n② 逐因子：文件权重 / 归零裁定 / 实际 runtime_weight（票权，已归一）")
    hits = 0
    for r in active:
        fid = str(r.get("factor_id") or "")
        rw = r.get("runtime_weight")
        icir = (r.get("scores") or {}).get("icir")
        is_zero = fid in zero
        if is_zero:
            hits += 1
        print(f"   {fid:<38} 文件={raw.get(fid, float('nan')):>7.4f} "
              f"{'★归零' if is_zero else '     '} runtime_weight={rw if rw is None else round(float(rw), 5)} "
              f"icir={icir}")
    print(f"\n③ 中长线活跃集 ∩ 学习层归零集 = {hits}")
    if hits:
        print("   ⇒ **成立**：学习层判定「IC<=0 不参与合成」的因子，仍以非零归一票权参与中长线投票。")
    else:
        print("   ⇒ 今日不成立：被归零的因子当前不在中长线活跃集内（潜在缺陷，非现行影响）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
