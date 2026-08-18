"""ICIR 加权组合权重解析（升级计划 v3.0 S3/M4 · P3）。

FACTOR_COMBO_MODE:
  - "icir"（默认）: w_i ∝ max(icir_i, 0) 归一；因子 scores.icir 由打分时写回，
    手工 data/factor_runtime_weights.json 条目为覆盖项（原语义保留）。
  - "equal": 旧行为（手工 json 缺省 1.0）。

短线/中线各自独立调用（两套 active 集互不混用）。
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)


def combo_mode() -> str:
    return str(os.environ.get("FACTOR_COMBO_MODE", "icir") or "icir").strip().lower()


def resolve_combo_weights(records: List[Dict], manual: Dict[str, float]) -> Dict[str, float]:
    """records: active 因子记录（含 scores.icir）；manual: 手工覆盖权重。"""
    if not records:
        return {}
    mode = combo_mode()
    if mode != "icir":
        return {str(r.get("factor_id") or ""): float(manual.get(str(r.get("factor_id") or ""), 1.0)) for r in records}
    base: Dict[str, float] = {}
    for r in records:
        fid = str(r.get("factor_id") or "")
        if not fid:
            continue
        if fid in manual:
            base[fid] = float(manual[fid] or 0.0)
        else:
            _icir = float((r.get("scores") or {}).get("icir") or 0.0)
            base[fid] = max(_icir, 0.0)
    tot = sum(base.values())
    if tot <= 0:
        n = max(len(base), 1)
        return {fid: 1.0 / n for fid in base}
    return {fid: v / tot for fid, v in base.items()}
