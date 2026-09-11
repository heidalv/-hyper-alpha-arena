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
            _scores = r.get("scores") or {}
            _icir = float(_scores.get("icir") or 0.0)
            # [item13 2026-08-21] 与路由同一套符号规则：以晋升时锁定的
            # expected_sign 为准，权重幅度 = max(expected_sign × icir, 0)。
            # ① 一致的反向因子（sign=-1, icir<0）获得正常权重（路由按
            #    expected_sign 反手使用它）；② 符号不一致（icir 与锁定方向
            #    相反）的因子不信任 → 权重 0。
            # [2026-08-26 修复] 旧记录无 expected_sign 时按 sign(icir) 锁定
            #（与路由 orient=expected_sign or sign(ic) 同规则），而不是恒 +1——
            # 恒 +1 会把负 ICIR 的 A/B 级反向因子（如 obv@4h/-0.489、obv@1d/-0.812，
            # OOS Sharpe 1.06/净收益+0.33）权重算成 0，静默踢出投票=中线无信号弹药。
            _sign = float(_scores.get("expected_sign") or (1.0 if _icir >= 0 else -1.0))
            base[fid] = max(_sign * _icir, 0.0)
            # [2026-08-26 防静默断点] 任何因子权重归零必须显式告警并给出原因
            if base[fid] <= 0.0 and abs(_icir) > 0.01:
                _why = ("icir符号与锁定expected_sign不一致(不信任)" if _scores.get("expected_sign")
                        else "icir<=0且无符号锁定")
                logger.warning(
                    "[ComboWeights] 因子权重归零: %s icir=%.4f expected_sign=%s -> %s",
                    fid, _icir, _scores.get("expected_sign"), _why,
                )
    tot = sum(base.values())
    if tot <= 0:
        # [2026-09-02 消除 fail-open] 此前这里回退均权(1/n)：把上面刚被负 IC /
        # 符号冲突"特意归零"的因子原封不动地还回等权，与归零的设计意图正好相反
        # ——因子体系整体失效时，系统反而按等权继续出信号。
        # 现在如实返回全零：下游 midlong_factor_route 的 weight_sum<=0 分支会给出
        # no_valid_votes 并跳过本轮（fail-closed，宁可不开仓也不按坏权重开仓）。
        logger.error(
            "[ComboWeights] 全部 %d 个因子权重归零 → 本轮不出信号（不再回退均权）。"
            "常见原因：icir 全为 0/负（检查 factor_active_set.icir 是否在刷新）、"
            "或 expected_sign 与 icir 普遍冲突", len(base),
        )
        return {fid: 0.0 for fid in base}
    return {fid: v / tot for fid, v in base.items()}
