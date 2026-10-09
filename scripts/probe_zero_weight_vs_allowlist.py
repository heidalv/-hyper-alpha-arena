# -*- coding: utf-8 -*-
"""[F346 探针] 零权重因子（学习层判定"不参与合成"）与实盘受治理白名单的交集。

只读：不改任何文件、不写库、不重启。
判定链：factor_ic_evaluator.py:441-442 写 weight=0（"IC<=0 → 不参与合成"）
        → load_runtime_factor_weights() 第 95 行 max(0.1, ...) 夹逼
        → factor_evaluation_pipeline.py:234 base_weights[name] *= rw（0.0 本可让
          `_aggregate` 的 `if w <= 0: continue` 完全跳过该因子）
⇒ 若零权重因子出现在实盘 allowlist 里，则"学习层投票排除"在运行时被降级为 10% 权重。
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WEIGHTS = ROOT / "data" / "factor_runtime_weights.json"


def raw_zero() -> set:
    w = json.loads(WEIGHTS.read_text(encoding="utf-8"))
    ws = w.get("weights") or {}
    return {str(k) for k, v in ws.items() if float(v or 0) == 0.0}, ws, w


def engine_allowlist() -> tuple:
    """复刻 base_factors.compute_all_factors 的 allowlist 构造（只读）。"""
    ids: set = set()
    src: dict = {}
    try:
        from backend.services.factor_engine.active_set_policy import (
            ActiveSetRole, load_factor_active_rows)
        from backend.services.factor_engine.key_utils import normalize_engine_key
        rows = load_factor_active_rows(ActiveSetRole.TRADABLE, parse_expr=False)
        skipped_seed = 0
        for r in rows:
            if str(r.get("source") or "").startswith("seed_bootstrap"):
                skipped_seed += 1
                continue
            fid = normalize_engine_key(str(r.get("factor_id") or ""))
            if fid:
                ids.add(fid)
                src.setdefault(fid, "TRADABLE")
        print(f"  TRADABLE 行数={len(rows)}（跳过 seed_bootstrap {skipped_seed} 个）")
    except Exception as e:
        print("  TRADABLE 读取失败:", str(e)[:200])
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        from backend.services.factor_engine.key_utils import normalize_engine_key
        rows = custom_factor_store.list_active(tenant_id=None)
        n0 = len(ids)
        for r in rows:
            fid = normalize_engine_key(str(r.get("factor_id") or ""))
            if fid:
                ids.add(fid)
                src.setdefault(fid, "store_active")
        print(f"  store active 行数={len(rows)}（新增 {len(ids) - n0}）")
    except Exception as e:
        print("  store active 读取失败:", str(e)[:200])
    return ids, src


def main() -> int:
    print("=" * 78)
    print("F346 探针：零权重因子 × 实盘受治理白名单")
    print("=" * 78)
    zero, ws, w = raw_zero()
    print(f"\n① 原始权重文件 {WEIGHTS.name}: 条目={len(ws)}  零权重={len(zero)}"
          f"  updated_at={w.get('updated_at')}")

    st = w.get("stats") or {}
    neg_ic = sorted(k for k, v in st.items()
                    if isinstance(v, dict) and v.get("ic") is not None and float(v["ic"]) <= 0)
    print(f"   stats 段 IC<=0 的因子={len(neg_ic)}（这些被写成 weight=0.0）")

    print("\n② 运行时读回口径（load_runtime_factor_weights）")
    try:
        from backend.services.factor_ic_evaluator import load_runtime_factor_weights
        loaded = load_runtime_factor_weights() or {}
        lz = sum(1 for v in loaded.values() if float(v) == 0.0)
        print(f"   读回条目={len(loaded)}  其中 ==0.0 的={lz}  ← 期望 {len(zero)}")
        for k in ("rsi", "zscore", "momentum", "roc", "ema_trend"):
            if k in ws:
                print(f"     {k:<12} 文件={float(ws[k]):.4f}  读回={float(loaded.get(k, float('nan'))):.4f}")
    except Exception as e:
        print("   读回失败:", str(e)[:200])

    print("\n③ 实盘受治理白名单")
    allow, src = engine_allowlist()
    print(f"   白名单规模={len(allow)}")
    inter = sorted(allow & zero)
    print(f"\n④ 交集（**在白名单里、但被学习层判为不参与**）={len(inter)}")
    for k in sorted(allow):
        mark = "★零权重" if k in zero else ""
        print(f"     {k:<44}{src.get(k,'?'):<12}{mark}")
    print("\n结论口径：交集非空 ⇒ 学习层「IC<=0 不参与」的判定在运行时被夹逼成 10% 权重，"
          "仍在参与合成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
