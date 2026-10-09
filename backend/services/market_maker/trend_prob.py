# -*- coding: utf-8 -*-
"""[h894 2026-10-07] 高频趋势概率方向 —— **纯标准库推理侧**(worker 可加载)。

用户定的方向:高频 = 趋势概率驱动的快进快出(不是做市);进场/出场都挂单
(Aster 挂单 0 手续费是生命线)。本模块负责「方向从哪来」:

    P(30s 内涨超阈值 | 当前 tick 特征) 与 P(跌超阈值 | 同) —— 加权朴素贝叶斯
    (分桶条件独立) + Platt 校准,与项目现有 cycle_direction_probability 同一家族,
    但特征是 tick 级(微价/OFI/20s/60s/120s 趋势/20s 波动),标签是 30s 前向中价收益。

为什么单独一个文件:worker 跑在 `.runtime\\Python312` 解释器,**没有 numpy**。
推理 = 查表 + 连加 + sigmoid,纯 Python 就够 ⇒ 本文件零第三方依赖。
训练(numpy)在 backend/services/evolution/hft_trend_prob_train.py,模型落 JSON,
两边共用本文件的 FEATURES / bucketize(口径唯一来源,防训练/推理漂移)。

fail-closed 纪律:模型文件缺失/过期/坏 ⇒ load_model 返回 None ⇒ worker 回退到
现有 direction_score 路径(行为与今天之前完全一致)。
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, Optional, Tuple

# 特征清单(训练/推理唯一口径)。worker 侧映射:
#   mp_skew_bp  = 顶档微价偏离(量加权 microprice vs 中价),bp
#   ofi         = 60s 主动买/卖量失衡 (bv−sv)/(bv+sv)
#   obi_top     = 顶档盘口失衡 (bid_qty0−ask_qty0)/(bid_qty0+ask_qty0)
#   trend_Ns    = trend_move_bp(mid_hist, N)   —— mid_hist 每拍(≈1s)一点
#   accel       = trend_20s − trend_60s(加速度:近端趋势 − 远端趋势)
#   vol_20s     = realized_vol_bp(mid_hist, 20)
# [h899 v2] 新增 obi_top/accel:盘口失衡是短线方向最强预测因子之一。
# (深层 obi_top5 因深度快照全排序太慢而砍掉——顶档失衡已抓住主要信号。)
FEATURES = ("mp_skew_bp", "ofi", "obi_top",
            "trend_20s", "trend_60s", "trend_120s", "accel", "vol_20s",
            # [2026-10-08 提准] 订单流加强:ofi 权重实测仅 0.096(被量纲压住),
            # 加「加速度」和「量加权」让资金信号真正被用上。
            "ofi_accel", "ofi_volw")

DEFAULT_MODEL = os.path.join("data", "hft_trend_prob", "model.json")
MODEL_MAX_AGE_SEC = 26 * 3600        # 模型超过 26h 未重训 ⇒ 回退旧路径
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def bucketize(edges: list, value: Optional[float]) -> int:
    """value 落到第几个桶(0..len(edges))。None ⇒ 中间桶(中性)。"""
    if value is None:
        return len(edges) // 2
    v = float(value)
    b = 0
    for e in edges:
        if v > float(e):
            b += 1
        else:
            break
    return b


def load_model(root: str, max_age_sec: float = MODEL_MAX_AGE_SEC,
               path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """读模型 JSON(mtime 缓存)。缺失/坏/过期 ⇒ None(不抛,fail-closed)。"""
    p = path or os.path.join(root, DEFAULT_MODEL)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    hit = _CACHE.get(p)
    if hit is not None and hit[0] == mt:
        model = hit[1]
    else:
        try:
            with open(p, encoding="utf-8") as fh:
                model = json.loads(fh.read())
        except Exception:
            return None
        _CACHE[p] = (mt, model)
    if max_age_sec > 0 and time.time() - float(model.get("trained_at") or 0.0) > max_age_sec:
        return None
    return model


def predict_proba(model: Dict[str, Any], feats: Dict[str, Optional[float]]
                  ) -> Tuple[float, float]:
    """返回 (p_up, p_dn):30s 内 涨/跌 超阈值的校准概率。"""
    tables = model["tables"]
    s_up = float(model["prior"]["up"])
    s_dn = float(model["prior"]["dn"])
    for f in model["features"]:
        t = tables[f]
        b = bucketize(t["edges"], feats.get(f))
        w = float(t["w"])
        s_up += w * float(t["ll_up"][b])
        s_dn += w * float(t["ll_dn"][b])
    a, b = model["platt"]["up"]
    p_up = sigmoid(float(a) * s_up + float(b))
    a, b = model["platt"]["dn"]
    p_dn = sigmoid(float(a) * s_dn + float(b))
    return p_up, p_dn


def predict_side_edge(root: str, feats: Dict[str, Optional[float]],
                      model: Optional[Dict[str, Any]] = None,
                      margin: Optional[float] = None,
                      ) -> Optional[Tuple[str, float]]:
    """带力度的方向判定。返回 (side, edge):
      side ∈ {"buy","sell",""}("" = 概率不够偏,本拍无方向);
      edge = 胜方概率 − 门槛(≥0,供「仓位随信心放大」用);
      模型不可用 ⇒ None(调用方回退旧路径)。

    margin:None ⇒ 用模型文件里的门槛;显式传值 ⇒ 按 基础率+margin 重算
    (worker 侧可用 MM_TREND_PROB_MARGIN 调激进度,不用重训模型)。
    """
    m = model if model is not None else load_model(root)
    if m is None:
        return None
    try:
        p_up, p_dn = predict_proba(m, feats)
    except Exception:
        return None
    gate = m.get("gate") or {}
    if margin is not None:
        met = m.get("metrics") or {}
        p_min_up = float(met.get("base_up") or 0.0) + float(margin)
        p_min_dn = float(met.get("base_dn") or 0.0) + float(margin)
    else:
        p_min_up = float(gate.get("p_min_up") or 0.5)
        p_min_dn = float(gate.get("p_min_dn") or 0.5)
    if p_up >= p_min_up and p_up > p_dn:
        return "buy", p_up - p_min_up
    if p_dn >= p_min_dn and p_dn > p_up:
        return "sell", p_dn - p_min_dn
    return "", 0.0


def predict_side(root: str, feats: Dict[str, Optional[float]],
                 model: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """方向判定主入口。返回:
      "buy" / "sell"  —— 概率够偏,给方向;
      ""              —— 模型在,但概率不够偏(本拍无方向);
      None            —— 模型不可用(缺失/过期/坏) ⇒ 调用方回退旧路径。
    """
    r = predict_side_edge(root, feats, model=model)
    return None if r is None else r[0]


def predict_magnitude(model: Optional[Dict[str, Any]],
                      feats: Dict[str, Optional[float]]) -> Optional[float]:
    """[h902 幅度预测] 带符号预期前向收益(bp)。纯点积,worker 的 .runtime 可跑。

    返回:E[fwd_bp](正=预计涨这么多,负=跌)。模型无 magnitude 节 ⇒ None。
    用途:只在 |E[fwd]| > 成本门槛时进场 ⇒ 治"赚小"的病根(赢单变大)。
    """
    if not model:
        return None
    mag = model.get("magnitude") or {}
    w = mag.get("w") or []
    if not w:
        return None
    try:
        feats_list = model.get("features") or list(FEATURES)
        s = float(mag.get("b") or 0.0)
        for j, fname in enumerate(feats_list):
            v = feats.get(fname)
            s += float(w[j]) * (float(v) if v is not None else 0.0)
        return s
    except Exception:
        return None


def held_edge_bp(root: str, feats: Dict[str, Optional[float]],
                 held_side: str, model: Optional[Dict[str, Any]] = None,
                 scale: float = 20.0) -> Optional[float]:
    """[h899d 2026-10-07] 持仓方向的**剩余概率优势**(bp)——信号驱动出场的核心。

    概率模型既管进场,也该管出场:进场是"概率说涨就买",出场就是
    "概率不再说涨就卖"。返回 (p_held − p_opposite) × scale:
      > 0  ⇒ 持仓方向仍有概率优势,继续持有;
      ≤ 0  ⇒ 优势消失/翻面,该离场(调用方触发 maker_edge,0 费)。
    模型不可用 ⇒ None(调用方回退到时间/止损兜底)。
    """
    m = model if model is not None else load_model(root)
    if m is None:
        return None
    try:
        p_up, p_dn = predict_proba(m, feats)
    except Exception:
        return None
    edge = (p_up - p_dn) if held_side == "buy" else (p_dn - p_up)
    return float(edge) * float(scale)
