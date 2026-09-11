"""挖矿适应度的统一目标：含成本净收益 + 分段 ICIR（P3.2 核心，2026-09-03）。

背景（两份独立审查一致指出）：
    GP-CPU（gp_miner._fitness_core）、GP-GPU（gp_gpu_eval.compute_fitness_from_values）、
    MCTS（mcts_miner._mcts_fitness_core）三条挖矿路径此前各自只优化 |IC| / ICIR。
    IC 只衡量"排序相关"，不衡量"扣掉手续费后还剩多少钱"：一个逐 bar 翻转的因子可以
    IC 很高、净收益为负；而晋升门禁（FactorBacktestScorer._walk_forward_backtest）
    恰恰按扣费后的 walk-forward 净收益打分 → 挖掘在优化一个与门禁不同的目标，
    大量候选在门禁前被白白淘汰（"挖掘分数过低"的机制之一）。

本模块给三条路径提供**同一份**目标实现：

1. ``segment_icir``：按币段（lens）计算 IC 的 mean/std；>=2 段且 std 非退化时给 ICIR，
   否则回退全面板 |IC|（与 GPU 路径 FIX-1 语义一致；此前 CPU 路径在无 GPU 上下文时
   拿不到 lens，"icir" 会静默退化成 |IC|——本模块让 GPMiner/MctsMiner 直接携带 lens）。

2. ``net_edge_after_cost``：sign 仓位策略的每步净收益（收益单位，非 bp）
       pos_k  = sign(f_k − median(f))          每 horizon 根**非重叠**调仓（h 个相位取平均，等价用满全部样本）
       gross  = |mean(pos_k · r_k)|            取更优方向（反向因子同样给分）
       cost   = FACTOR_SCORER_COST · mean(|Δpos_k| / 2)   每条腿 cost/2，一次翻转≈一次往返
       net    = gross − cost
   r_k 永远是**真实的未来 horizon 根简单收益**（与三重障碍标签无关——标签是 ±1，扣不了 bp）。
   逐段（逐币）计算，不跨币携带仓位。口径逐项对齐 FactorBacktestScorer._walk_forward_backtest
   （sign 仓位、非重叠 h 根、cost·turn/2、同一 FACTOR_SCORER_COST 成本源）。

3. ``blend_objective``：按 objective 把净收益并进目标（FACTOR_GP_OBJECTIVE，一个开关管三条路径）
       "ic" / "icir"        旧行为，不加净收益项（可回滚）
       "icir_net"（默认）   obj = ICIR + w · clip(net / cost, −2, 2)
       "ic_net"             obj = |IC| + w · clip(net / cost, −2, 2)
       "net"                obj = clip(net / cost, −2, 2)；|IC| < FACTOR_GP_IC_PRESCREEN 直接 −inf（IC 仅做初筛）
   net/cost 是无量纲"成本倍数"：−1 = 白付一次成本，0 = 恰好回本，+1 = 净赚一倍成本。
   w = FACTOR_GP_NET_WEIGHT（默认 0.5）：与准入 ICIR 门槛 0.4（lifecycle.min_icir）同量级，
   使"能否回本"与"信号稳定度"在适应度里同等重要，而不是只看 IC。

所有函数纯 numpy、无状态、可被 loky worker 反序列化后直接调用。
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_NEG_INF = float("-inf")

#: 会把净收益并入目标的 objective 取值
NET_OBJECTIVES = ("net", "icir_net", "ic_net")
#: 以 ICIR 为基础项的 objective 取值
ICIR_OBJECTIVES = ("icir", "icir_net")
#: 默认目标（P3.2：ICIR + 含成本净收益）
DEFAULT_OBJECTIVE = "icir_net"
#: net/cost 比值的裁剪上下限（防成本极小时爆炸）
NET_RATIO_CLIP = 2.0

_EMPTY_EDGE = {"net": float("nan"), "gross": float("nan"), "turnover": float("nan"),
               "n": 0, "orient": 0}


# ─────────────────────────── 配置读取 ───────────────────────────

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def default_objective() -> str:
    """FACTOR_GP_OBJECTIVE（GP/MCTS 共用一个开关）；未设或非法值 → icir_net。"""
    raw = (os.getenv("FACTOR_GP_OBJECTIVE") or "").strip().lower()
    if raw in ("ic", "icir") or raw in NET_OBJECTIVES:
        return raw
    return DEFAULT_OBJECTIVE


def objective_uses_net(objective: Optional[str]) -> bool:
    return (objective or "").strip().lower() in NET_OBJECTIVES


def objective_uses_icir(objective: Optional[str]) -> bool:
    return (objective or "").strip().lower() in ICIR_OBJECTIVES


def mining_cost() -> float:
    """挖矿净收益用的往返成本（收益单位，如 0.0009 = 9bp）。

    优先级：FACTOR_MINE_COST（挖矿专用覆盖）→ settings.FACTOR_SCORER_COST（与晋升门禁
    同一来源，保证"挖什么"与"门禁量什么"一致）→ 0.0009。
    """
    override = os.getenv("FACTOR_MINE_COST")
    if override:
        try:
            v = float(override)
            if np.isfinite(v) and v >= 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        from backend.config import settings as _s
        v = float(getattr(_s, "FACTOR_SCORER_COST", 0.0009))
        if np.isfinite(v) and v >= 0:
            return v
    except Exception:
        pass
    return 0.0009


def net_weight() -> float:
    """FACTOR_GP_NET_WEIGHT：净收益项权重（默认 0.5）。"""
    return max(0.0, _env_float("FACTOR_GP_NET_WEIGHT", 0.5))


def ic_prescreen() -> float:
    """FACTOR_GP_IC_PRESCREEN：objective="net" 时的 |IC| 初筛下限（默认 0.005）。"""
    return max(0.0, _env_float("FACTOR_GP_IC_PRESCREEN", 0.005))


# ─────────────────────────── 分段工具 ───────────────────────────

def segment_bounds(lens: Optional[Sequence[int]], n: int) -> list[tuple[int, int]]:
    """lens → [(start, end), ...]；lens 为空/不合法（总长≠n）时视为单段 [0, n)。"""
    if not lens:
        return [(0, n)] if n > 0 else []
    out: list[tuple[int, int]] = []
    off = 0
    for ln in lens:
        try:
            ln = int(ln)
        except (TypeError, ValueError):
            return [(0, n)] if n > 0 else []
        if ln <= 0:
            continue
        out.append((off, off + ln))
        off += ln
    if off != n:
        return [(0, n)] if n > 0 else []
    return out


def segment_icir(
    fv: np.ndarray,
    target: np.ndarray,
    mask: np.ndarray,
    lens: Optional[Sequence[int]],
    fallback: float,
    min_seg_samples: int = 20,
) -> float:
    """按币段 IC 的 |mean|/std；不足 2 个有效段或 std 退化 → fallback（通常是全面板 |IC|）。

    与 gp_gpu_eval.compute_fitness_from_values 的 FIX-1 语义逐项一致：
    单段 std 精确为 0 时 mean/1e-10 会爆到 1e8，碾压所有惩罚项，因此必须回退。
    """
    if not lens:
        return float(fallback)
    n = int(len(fv))
    case_list: list[float] = []
    for a, b in segment_bounds(lens, n):
        mseg = mask[a:b]
        if int(mseg.sum()) < min_seg_samples:
            continue
        fs = fv[a:b][mseg]
        ts = target[a:b][mseg]
        if np.std(fs) < 1e-12 or np.std(ts) < 1e-12:
            continue
        c = float(np.corrcoef(fs, ts)[0, 1])
        if np.isfinite(c):
            case_list.append(c)
    if len(case_list) < 2:
        return float(fallback)
    arr = np.asarray(case_list, dtype=float)
    std = float(arr.std())
    if std <= 1e-3:
        return float(fallback)
    return abs(float(arr.mean() / std))


# ─────────────────────────── 含成本净收益 ───────────────────────────

def net_edge_after_cost(
    fv: np.ndarray,
    fwd_ret: np.ndarray,
    *,
    horizon: int,
    cost: float,
    lens: Optional[Sequence[int]] = None,
    min_samples: int = 50,
) -> dict:
    """sign 仓位、每 horizon 根非重叠调仓的每步净收益（收益单位）。

    返回 dict：
        net      每步净收益 = gross − cost·turnover（NaN = 样本不足/无法计算）
        gross    每步毛收益 |mean(pos·r)|（已取更优方向）
        turnover 每步平均换手（单位：往返次数，0→±1 记 0.5，+1↔−1 记 1）
        n        参与统计的步数（= 有效样本数；每个有效 bar 恰好落在一个相位里）
        orient   更优方向（+1 因子值高做多；−1 反向）
    """
    try:
        fv = np.asarray(fv, dtype=float)
        r = np.asarray(fwd_ret, dtype=float)
    except Exception:
        return dict(_EMPTY_EDGE)
    if fv.ndim != 1 or fv.shape != r.shape or fv.size == 0:
        return dict(_EMPTY_EDGE)
    h = max(1, int(horizon or 1))
    n = int(fv.size)
    finite = np.isfinite(fv) & np.isfinite(r)
    if int(finite.sum()) < int(min_samples):
        return dict(_EMPTY_EDGE)
    med = float(np.median(fv[finite]))
    gross_sum = 0.0
    turn_sum = 0.0
    n_steps = 0
    for a, b in segment_bounds(lens, n):
        idx = np.flatnonzero(finite[a:b]) + a
        if idx.size == 0:
            continue
        # 与 scorer 一致：先压掉 NaN（预热期通常在段首），再按 h 根非重叠采样；
        # 逐相位（o = 0..h−1）统计后合并，等价于把全部有效样本都用上且每样本只算一次。
        pos_seg = np.sign(fv[idx] - med)
        r_seg = r[idx]
        for o in range(min(h, idx.size)):
            pk = pos_seg[o::h]
            rk = r_seg[o::h]
            if pk.size == 0:
                continue
            gross_sum += float(np.dot(pk, rk))
            prev = np.concatenate(([0.0], pk[:-1]))
            turn_sum += float(np.abs(pk - prev).sum()) / 2.0
            n_steps += int(pk.size)
    if n_steps < int(min_samples):
        return dict(_EMPTY_EDGE)
    gross_signed = gross_sum / n_steps
    turnover = turn_sum / n_steps
    orient = 1 if gross_signed >= 0 else -1
    gross = abs(gross_signed)
    net = gross - float(cost) * turnover
    if not (np.isfinite(net) and np.isfinite(gross) and np.isfinite(turnover)):
        return dict(_EMPTY_EDGE)
    return {"net": float(net), "gross": float(gross), "turnover": float(turnover),
            "n": int(n_steps), "orient": int(orient)}


def net_ratio(net: float, cost: float) -> float:
    """净收益 / 成本 → 无量纲成本倍数，裁剪到 ±NET_RATIO_CLIP。"""
    if not np.isfinite(net):
        return float("nan")
    denom = float(cost) if (cost is not None and cost > 1e-6) else 1e-6
    return float(np.clip(net / denom, -NET_RATIO_CLIP, NET_RATIO_CLIP))


# ─────────────────────────── 目标融合 ───────────────────────────

def net_context(
    *,
    objective: Optional[str],
    fwd_ret: Optional[np.ndarray],
    horizon: Optional[int],
    lens: Optional[Sequence[int]] = None,
    cost: Optional[float] = None,
    weight: Optional[float] = None,
    prescreen: Optional[float] = None,
) -> dict:
    """构造可序列化的净收益求值上下文（GPMiner/MctsMiner._fitness_state 直接塞进 state）。

    objective 不含 net 或 fwd_ret 缺失 → 返回 {"objective": objective}（blend_objective 原样返回基础目标）。
    """
    obj = (objective or "").strip().lower() or DEFAULT_OBJECTIVE
    ctx: dict = {"objective": obj}
    if not objective_uses_net(obj):
        return ctx
    if fwd_ret is None:
        logger.debug("[FitnessObjective] objective=%s 但未提供 fwd_ret，净收益项停用", obj)
        return ctx
    ctx.update({
        "fwd_ret": np.asarray(fwd_ret, dtype=float),
        "horizon": max(1, int(horizon or 1)),
        "lens": list(lens) if lens else None,
        "cost": float(cost if cost is not None else mining_cost()),
        "weight": float(weight if weight is not None else net_weight()),
        "prescreen": float(prescreen if prescreen is not None else ic_prescreen()),
    })
    return ctx


def blend_objective(
    base_obj: float,
    ic_abs: float,
    fv: np.ndarray,
    ctx: Optional[dict],
    *,
    min_samples: int = 50,
) -> float:
    """把含成本净收益并入基础目标（|IC| 或 ICIR）。

    - objective ∈ {ic, icir} 或 ctx 无 fwd_ret → 原样返回 base_obj（旧行为）
    - 净收益算不出（样本不足）→ 原样返回 base_obj（不因数据边界误杀）
    - "net"：|IC| < prescreen → −inf；否则 clip(net/cost)
    - "icir_net"/"ic_net"：base_obj + weight · clip(net/cost)
    """
    if not ctx:
        return float(base_obj)
    obj = (ctx.get("objective") or "").strip().lower()
    if not objective_uses_net(obj):
        return float(base_obj)
    fwd_ret = ctx.get("fwd_ret")
    if fwd_ret is None:
        return float(base_obj)
    edge = net_edge_after_cost(
        fv, fwd_ret,
        horizon=int(ctx.get("horizon") or 1),
        cost=float(ctx.get("cost") if ctx.get("cost") is not None else mining_cost()),
        lens=ctx.get("lens"),
        min_samples=min_samples,
    )
    ratio = net_ratio(edge["net"], float(ctx.get("cost") or mining_cost()))
    if not np.isfinite(ratio):
        return float(base_obj)
    if obj == "net":
        if float(ic_abs) < float(ctx.get("prescreen") if ctx.get("prescreen") is not None else ic_prescreen()):
            return _NEG_INF
        return float(ratio)
    w = float(ctx.get("weight") if ctx.get("weight") is not None else net_weight())
    return float(base_obj + w * ratio)


__all__ = [
    "NET_OBJECTIVES", "ICIR_OBJECTIVES", "DEFAULT_OBJECTIVE", "NET_RATIO_CLIP",
    "default_objective", "objective_uses_net", "objective_uses_icir",
    "mining_cost", "net_weight", "ic_prescreen",
    "segment_bounds", "segment_icir", "net_edge_after_cost", "net_ratio",
    "net_context", "blend_objective",
]
