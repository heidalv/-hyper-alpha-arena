# -*- coding: utf-8 -*-
"""[2026-10-08 重设计] 趋势强度分 TrendScore —— 选币与末位淘汰的唯一排序键。

用户定的方向：高频 = 趋势概率驱动的快进快出（不是做市商）。
选币第一看「此刻短期趋势强弱」，历史盈亏只踢灾难、不排序。

设计要点（与 research_l1/out/选币与末位淘汰重设计_20261008.md 一致）：
  · 订单流 OFI 是**领先**信号（资金先进、价格后跟），权重最大；
  · 趋势概率（trend_prob，OOS AUC 0.67）与 OFI 同向 ⇒ 双确认加分；
  · 趋势干净度（净位移/区间）防「来回乱抖」——乱抖的趋势没有预测力；
  · 流动性（成交笔数/价差）是**门槛**不是分数——不够格直接出局；
  · 历史盈亏**不参与排序**，只用于踢单笔滑穿的灾难币。

为什么单独一个文件 + 纯标准库：worker 跑在 `.runtime\\Python312`，**没有 numpy**。
本模块零第三方依赖，worker 与训练/审计两侧都能 import。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ── 门槛（流动性，只是「门」） ──
# [2026-10-08 用户规则] 高频 = 信号强 + 交易量大。量小的币挂单没人吃，
# 不配叫高频。FLOW_MIN 从 8 提到 30：只留「挂单有人打」的活跃币。
FLOW_MIN_30M = 10         # 近30分钟成交笔数下限（不够=挂单没人吃，踢）
SPREAD_MAX_BP = 25.0      # 当前价差上限（太宽=跳空/盘口坏掉）

# ── 分数权重 ──
W_OFI = 50.0              # 订单流强度（领先信号，权重最大）
W_PROB = 30.0             # 趋势概率双确认
W_CLEAN = 20.0            # 趋势干净度

# ── 判定阈值 ──
OFI_STRONG = 0.15         # |OFI| ≥ 此值算「劲够足」
CLEAN_MIN = 0.30          # 净位移/区间 ≥ 此值算「单边走」（<1 成来回抖）
SCORE_ENTER = 8.0        # 进候选池的最低分（宽松些,名单别一下缩太小）
SCORE_STAY = 4.0         # 在册币跌破此分 ⇒ 末位淘汰换出


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def ofi_score(ofi: Optional[float]) -> float:
    """订单流强度分 0~50。|OFI| 0.15 起给分，0.4 封顶满分。"""
    if ofi is None:
        return 0.0
    a = abs(float(ofi))
    if a < OFI_STRONG:
        return 0.0
    # 0.15→0 分，0.40→50 分，线性
    return clamp((a - OFI_STRONG) / (0.40 - OFI_STRONG) * W_OFI, 0.0, W_OFI)


def clean_score(net_bp: Optional[float], range_bp: Optional[float]) -> float:
    """趋势干净度 0~20。净位移/区间越大越单边。区间太小（<3bp）不算。"""
    if net_bp is None or range_bp is None:
        return 0.0
    rng = float(range_bp)
    if rng < 3.0:
        return 0.0
    ratio = abs(float(net_bp)) / rng
    if ratio < CLEAN_MIN:
        return 0.0
    # 0.30→0 分，1.0→20 分
    return clamp((ratio - CLEAN_MIN) / (1.0 - CLEAN_MIN) * W_CLEAN, 0.0, W_CLEAN)


def prob_score(ofi_dir: int, prob_side: Optional[str],
               prob_edge: Optional[float]) -> float:
    """趋势概率双确认 0~30。概率方向与订单流同向才给分，edge 越大分越高。"""
    if not prob_side or ofi_dir == 0:
        return 0.0
    prob_dir = 1 if prob_side == "buy" else -1
    if prob_dir != ofi_dir:
        return 0.0
    edge = max(0.0, float(prob_edge or 0.0))
    # edge 0.02→约一半分，0.06→满分
    return clamp(edge / 0.06 * W_PROB, 0.0, W_PROB)


def trend_score(ofi: Optional[float],
                net_bp: Optional[float],
                range_bp: Optional[float],
                prob_side: Optional[str] = None,
                prob_edge: Optional[float] = None,
                ) -> Dict[str, Any]:
    """算一个币的趋势强度分。返回 {score, dir, parts}。

    dir: +1 涨 / -1 跌 / 0 无方向（OFI 太弱）。
    """
    ofi_dir = 0
    if ofi is not None:
        if float(ofi) >= OFI_STRONG:
            ofi_dir = 1
        elif float(ofi) <= -OFI_STRONG:
            ofi_dir = -1
    parts = {
        "ofi": round(ofi_score(ofi), 1),
        "prob": round(prob_score(ofi_dir, prob_side, prob_edge), 1),
        "clean": round(clean_score(net_bp, range_bp), 1),
    }
    score = clamp(sum(parts.values()))
    return {"score": round(score, 1), "dir": ofi_dir, "parts": parts}


def liquidity_ok(flow_30m: Optional[int], spread_bp: Optional[float]) -> bool:
    """流动性门槛（只是门）。成交够 + 价差不太宽。"""
    if flow_30m is None or int(flow_30m) < FLOW_MIN_30M:
        return False
    if spread_bp is None or float(spread_bp) <= 0 or float(spread_bp) > SPREAD_MAX_BP:
        return False
    return True
