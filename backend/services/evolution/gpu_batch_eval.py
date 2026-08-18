"""GPU 批量表达式求值器 — 栈式执行器（M1.5 + M2，2026-08-17 接线启用）。

针对 factor_engine DSL（expr/ops.py）的挖矿 AST 求值：
  - ast（{"op": .., "args": [...]} / {"f": ..} / {"c": ..}）→ 后序(postfix)编译 →
    数值化程序表（op_code/param/const/field_idx）；
  - 操作数栈 (P, MAXSTK, S, B) + 每程序指针，按步 × 算子分组掩码向量化执行，
    每个 (步, 算子) 一次核启动覆盖全部命中程序；
  - 滚动算子与 numpy/DSL 语义对齐：
      NaN 剔除（窗口内只统计 finite）、min_count=max(2, w//2)、窗口头 w-1 根 NaN；
      mean/sum/std/var 走 float64 cumsum（避免灾难性消去）；
      max/min/wma/decay_linear/ts_rank/ts_argmax/ts_argmin/corr/cov/ts_corr
      走 unfold 视图 + 掩码归约（无完整 (B,w) 物化，受显存预算约束）；
  - ema（串行递归）与显存超预算的 unfold 算子 → 该程序整体走 CPU 兜底（混合模式）。

设计文档：docs/GPU_FACTOR_EVAL_DESIGN.md（§2 栈式布局，§8 M1 实测结论）。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── DSL 算子分类（与 expr/ops.py OP_REGISTRY 对齐） ──────────────────

# 末参为常量参数的算子（窗口/标量不进栈）
_PARAM_OPS = {
    "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank", "delta",
    "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin", "scale",
    "corr", "cov", "ts_corr",
}
# 双序列参数算子（arity=3：x, y, w）
_PAIR_OPS = {"corr", "cov", "ts_corr"}
# 单序列参数算子（arity=2：x, w）
_UNARY_PARAM_OPS = _PARAM_OPS - _PAIR_OPS
# 普通一元算子
_UNARY_OPS = {"abs", "sign", "log", "sqrt"}
# 普通二元算子
_BINARY_OPS = {"add", "sub", "mul", "div", "pow", "greater", "less"}

# GPU 不支持的算子（整体程序 CPU 兜底）
_CPU_ONLY_OPS = {"ema", "scale"}

# 算子码表（0=nop, 1=const, 2=field, 3..=op）
_OP_CODES: Dict[str, int] = {}
for _i, _n in enumerate(
    ["const", "field"]
    + sorted(_UNARY_OPS)
    + sorted(_BINARY_OPS)
    + sorted(_UNARY_PARAM_OPS)
    + sorted(_PAIR_OPS),
    start=1,
):
    _OP_CODES[_n] = _i
_OP_CODE_NAMES = {v: k for k, v in _OP_CODES.items()}
_CODE_NOP = 0

_FINITE_MIN_COUNT = lambda w: max(2, int(w) // 2)  # noqa: E731


# ── 编译：AST → 后序 tokens ─────────────────────────────────────────

def _is_pure_const(node: Any) -> bool:
    """子树是否纯常量（无字段叶子）。numpy 语义下其求值结果为 0-d 标量。"""
    if not isinstance(node, dict):
        return True
    if "f" in node:
        return False
    if "c" in node:
        return True
    return all(_is_pure_const(a) for a in node.get("args", []))


def _ast_to_postfix(node: Any, tokens: List[Tuple[Any, ...]]) -> bool:
    """DFS 后序展开；返回 False 表示含 GPU 不支持算子（整体走 CPU）。"""
    if not isinstance(node, dict):
        return False
    if "f" in node:
        tokens.append(("field", str(node["f"])))
        return True
    if "c" in node:
        tokens.append(("const", float(node["c"])))
        return True
    if "op" not in node:
        return False
    op = str(node["op"])
    if op in _CPU_ONLY_OPS:
        return False
    args = list(node.get("args") or [])
    if op in _PARAM_OPS:
        n_data = 3 if op in _PAIR_OPS else 2
        if len(args) < n_data:
            return False
        data_args = args[: n_data - 1]
        param_node = args[n_data - 1]
        param = float(param_node.get("c", 0.0)) if isinstance(param_node, dict) else 0.0
        # 常量/纯常量操作数：numpy _a1d 使其截断为 (1,) → 面板拼接长度不符 →
        # 真实路径 fitness=-inf。GPU 广播会产生假阳性信号，必须整体 CPU 兜底复现 -inf。
        if any(_is_pure_const(a) for a in data_args):
            return False
        ok = True
        for a in data_args:
            ok = _ast_to_postfix(a, tokens) and ok
        tokens.append(("op", op, param))
        return ok
    # 普通算子（一元/二元），所有参数都是数据操作数
    ok = True
    for a in args:
        ok = _ast_to_postfix(a, tokens) and ok
    tokens.append(("op", op, None))
    return ok


def compile_ast_batch(
    asts: Sequence[dict],
    field_order: Sequence[str],
) -> Optional[Dict[str, np.ndarray]]:
    """AST 列表 → 数值化程序表（含 GPU 不支持算子时返回 None）。

    返回：
      op_code (P,L) int16   0=nop, 1=const, 2=field, 3..=算子
      param   (P,L) int16   滚动算子的窗口/滞后（截断取整）
      const   (P,L) float32 常量值
      field   (P,L) int16   字段索引（field_order 中的位置）
      stack_need (P,) int32 各程序所需最大栈深
      gpu_ok  (P,) bool     False=需 CPU 兜底
    """
    field_map = {f: i for i, f in enumerate(field_order)}
    programs: List[Optional[Tuple[List[Tuple[Any, ...]], int]]] = []
    max_len = 0
    max_stack = 0
    for ast in asts:
        tokens: List[Tuple[Any, ...]] = []
        ok = _ast_to_postfix(ast, tokens)
        if not ok:
            programs.append(None)
            continue
        # 未知字段 → CPU 兜底
        if any(t[0] == "field" and t[1] not in field_map for t in tokens):
            programs.append(None)
            continue
        # 计算栈深需求（模拟后序执行）
        depth = 0
        need = 0
        for t in tokens:
            if t[0] in ("const", "field"):
                depth += 1
                need = max(need, depth)
            else:
                _, op, _p = t
                arity = 2 if op in (_BINARY_OPS | _PAIR_OPS) else 1
                depth = depth - arity + 1
                need = max(need, depth)
        programs.append((tokens, need))
        max_len = max(max_len, len(tokens))
        max_stack = max(max_stack, need)
    if not any(p for p in programs):
        return None
    if max_len == 0 or max_stack == 0:
        return None

    P = len(asts)
    L = max_len
    op_code = np.zeros((P, L), dtype=np.int16)
    param = np.zeros((P, L), dtype=np.int16)
    const = np.zeros((P, L), dtype=np.float32)
    field = np.full((P, L), -1, dtype=np.int16)
    stack_need = np.ones(P, dtype=np.int32)
    gpu_ok = np.zeros(P, dtype=bool)
    # 每程序 unfold 类算子 (op, w) 列表：供面板层做显存预算过滤
    unfold_params: List[List[Tuple[str, int]]] = [[] for _ in range(P)]
    _UNFOLD_FAMILY = {"max", "min", "wma", "decay_linear", "ts_rank",
                      "ts_argmax", "ts_argmin", "corr", "cov", "ts_corr"}

    for p, prog in enumerate(programs):
        if prog is None:
            gpu_ok[p] = False
            continue
        tokens, need = prog
        stack_need[p] = need
        gpu_ok[p] = True
        for s, t in enumerate(tokens):
            if t[0] == "field":
                op_code[p, s] = 2
                field[p, s] = field_map[t[1]]
            elif t[0] == "const":
                op_code[p, s] = 1
                const[p, s] = t[1]
            else:
                _, op, prm = t
                op_code[p, s] = _OP_CODES[op]
                if prm is not None:
                    wv = max(1, int(round(float(prm))))
                    param[p, s] = wv
                    if op in _UNFOLD_FAMILY:
                        unfold_params[p].append((op, wv))
    return {
        "op_code": op_code,
        "param": param,
        "const": const,
        "field": field,
        "stack_need": stack_need,
        "gpu_ok": gpu_ok,
        "unfold_params": unfold_params,
        "max_stack": int(max_stack),
        "max_len": L,
    }


# ── torch 滚动算子（与 formula_ops 语义对齐） ────────────────────────

def _roll_head_nan(y: "torch.Tensor", w: int) -> "torch.Tensor":
    """(..., B-w+1) → (..., B)，头部 w-1 根 NaN。"""
    import torch

    if w <= 1:
        return y
    pad = torch.full(y.shape[:-1] + (w - 1,), float("nan"), device=y.device, dtype=y.dtype)
    return torch.cat([pad, y], dim=-1)


def _cumsum_roll(x: "torch.Tensor", w: int) -> Tuple["torch.Tensor", "torch.Tensor"]:
    """NaN 剔除滚动和与计数（float64 累加）。返回 (win_sum, win_cnt)，长度 B-w+1。"""
    import torch

    if w <= 0:
        w = 1
    fin = torch.isfinite(x)
    x64 = torch.where(fin, x.double(), torch.zeros((), dtype=torch.float64, device=x.device))
    pad = torch.nn.functional.pad(x64, (w, 0), mode="constant", value=0.0)
    fpad = torch.nn.functional.pad(fin.double(), (w, 0), mode="constant", value=0.0)
    c = torch.cumsum(pad, dim=-1)
    cnt = torch.cumsum(fpad, dim=-1)
    s = c[..., w:] - c[..., :-w]
    n = cnt[..., w:] - cnt[..., :-w]
    return s, n


def _roll_mean(x: "torch.Tensor", w: int) -> "torch.Tensor":
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    s, n = _cumsum_roll(x, w)
    valid = (n >= _FINITE_MIN_COUNT(w)) & _pos_gate(x, w)
    out = torch.where(valid, s / n.clamp(min=1), torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return out


def _roll_sum(x: "torch.Tensor", w: int) -> "torch.Tensor":
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    s, n = _cumsum_roll(x, w)
    valid = (n >= _FINITE_MIN_COUNT(w)) & _pos_gate(x, w)
    out = torch.where(valid, s, torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return out


def _pos_gate(x: "torch.Tensor", w: int) -> "torch.Tensor":
    """位置门：numpy _rolling 只对 i ≥ w-1 产出值。"""
    import torch

    B = x.shape[-1]
    idx = torch.arange(B, device=x.device)
    return idx >= (w - 1)


def _roll_std_var(x: "torch.Tensor", w: int, do_sqrt: bool) -> "torch.Tensor":
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    fin = torch.isfinite(x)
    x64 = x.double()
    # 全局中心化（仅 finite）：消除 E[x²]-E[x]² 的大数消去；NaN 位置必须保持 0 贡献
    cnt_all = fin.sum(dim=-1, keepdim=True).clamp(min=1)
    g = torch.where(fin, x64, torch.zeros((), dtype=torch.float64, device=x.device)).sum(
        dim=-1, keepdim=True
    ) / cnt_all
    xc = torch.where(fin, x64 - g, torch.zeros((), dtype=torch.float64, device=x.device))
    pad = torch.nn.functional.pad(xc, (w, 0), mode="constant", value=0.0)
    fpad = torch.nn.functional.pad(fin.double(), (w, 0), mode="constant", value=0.0)
    c = torch.cumsum(pad, dim=-1)
    c2 = torch.cumsum(pad * pad, dim=-1)
    cnt = torch.cumsum(fpad, dim=-1)
    s = c[..., w:] - c[..., :-w]
    s2 = c2[..., w:] - c2[..., :-w]
    n = cnt[..., w:] - cnt[..., :-w]
    valid = (n >= _FINITE_MIN_COUNT(w)) & _pos_gate(x, w)
    var = torch.clamp(s2 / n.clamp(min=1) - (s / n.clamp(min=1)) ** 2, min=0.0)
    out = var.sqrt() if do_sqrt else var
    out = torch.where(valid, out, torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return out


def _unfold_budget_ok(chunk: int, S: int, B: int, w: int, mem_mb: float) -> bool:
    """unfold 类算子瞬态显存预算检查（float64：≈ 3× (K,S,B-w+1,w) × 8B）。"""
    elems = int(chunk) * int(S) * max(0, int(B) - int(w) + 1) * int(w)
    est_bytes = elems * 8.0 * 3.0  # 视图 + 掩码/cnt 中间 + 输出，粗略 3×
    return est_bytes <= float(mem_mb) * 1e6


def _unfold_finite(x: "torch.Tensor", w: int):
    """unfold 视图 + finite 掩码 + 计数（float64，避免 int/int → float32）。返回 (xu, M, cnt, valid)。"""
    import torch

    xu = x.unfold(-1, w, 1)                      # (..., B-w+1, w) 视图
    M = torch.isfinite(xu)
    cnt = M.sum(dim=-1).double()                 # (..., B-w+1)
    valid = cnt >= _FINITE_MIN_COUNT(w)
    return xu, M, cnt, valid


def _roll_max_min(x: "torch.Tensor", w: int, is_max: bool) -> "torch.Tensor":
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    xu, M, cnt, valid = _unfold_finite(x, w)
    fill = float("-inf") if is_max else float("inf")
    xm = torch.where(M, xu, torch.full((), fill, dtype=torch.float64, device=x.device))
    out = xm.amax(dim=-1) if is_max else xm.amin(dim=-1)
    out = torch.where(valid, out, torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return _roll_head_nan(out, w)


def _roll_wma(x: "torch.Tensor", w: int) -> "torch.Tensor":
    """decay_linear/wma：按窗口内有效值的先后线性加权（近者权重高），与 formula_ops 一致。"""
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    xu, M, cnt, valid = _unfold_finite(x, w)
    # r = 有效值在其窗口内的序号（最老=1 起）
    r = torch.cumsum(M.float(), dim=-1)
    wsum = (M.float() * xu * r).sum(dim=-1)
    wnorm = (M.float() * r).sum(dim=-1)
    out = torch.where(valid, wsum / wnorm.clamp(min=1), torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return _roll_head_nan(out, w)


def _roll_ts_rank(x: "torch.Tensor", w: int) -> "torch.Tensor":
    """滚动排名：窗口内最后一个有效值在有效值中的百分位（0..1）。"""
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    xu, M, cnt, valid = _unfold_finite(x, w)
    # 最后一个有效值：cumsum(M) == cnt 的最右位置
    cs = torch.cumsum(M.float(), dim=-1)
    last_pos = (cs == cnt.unsqueeze(-1)).to(torch.float32).argmax(dim=-1)
    last_val = torch.gather(xu, -1, last_pos.unsqueeze(-1)).squeeze(-1)
    le = M & (xu <= last_val.unsqueeze(-1))
    rank = le.sum(dim=-1) / cnt.clamp(min=1)
    out = torch.where(valid, rank, torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return _roll_head_nan(out, w)


def _roll_argmaxmin(x: "torch.Tensor", w: int, is_max: bool) -> "torch.Tensor":
    """ts_argmax/ts_argmin：窗口内极值距当前的位置（0=当前），按有效值数归一化。"""
    import torch

    if w > x.shape[-1]:
        return torch.full_like(x, float("nan"))
    xu, M, cnt, valid = _unfold_finite(x, w)
    fill = float("-inf") if is_max else float("inf")
    xm = torch.where(M, xu, torch.full((), fill, dtype=torch.float64, device=x.device))
    idx = xm.argmax(dim=-1) if is_max else xm.argmin(dim=-1)
    # idx 是未压缩窗口内的位置 → 换算为该位置前的有效值个数（压缩索引）
    cs = torch.cumsum(M.float(), dim=-1)
    compact_idx = torch.gather(cs, -1, idx.unsqueeze(-1)).squeeze(-1) - 1.0
    norm = (cnt - 1.0 - compact_idx) / (cnt - 1.0).clamp(min=1)
    out = torch.where(valid, norm, torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return _roll_head_nan(out, w)


def _roll_pair_corr(x: "torch.Tensor", y: "torch.Tensor", w: int, as_cov: bool) -> "torch.Tensor":
    """ts_corr/corr/cov：有效交集窗口滚动相关/协方差（float64，std 门控 → 0）。"""
    import torch

    if w > min(x.shape[-1], y.shape[-1]):
        return torch.full_like(x, float("nan"))
    xu = x.unfold(-1, w, 1)
    yu = y.unfold(-1, w, 1)
    M = torch.isfinite(xu) & torch.isfinite(yu)
    cnt = M.sum(dim=-1).double()
    valid = cnt >= _FINITE_MIN_COUNT(w)
    # 全局/窗口中心化：协方差平移不变，消除 E[xy]-E[x]E[y] 大数消去
    xraw = torch.where(M, xu.double(), torch.zeros((), dtype=torch.float64, device=x.device))
    yraw = torch.where(M, yu.double(), torch.zeros((), dtype=torch.float64, device=x.device))
    mx = xraw.sum(-1, keepdim=True) / cnt.clamp(min=1).unsqueeze(-1)
    my = yraw.sum(-1, keepdim=True) / cnt.clamp(min=1).unsqueeze(-1)
    xv = torch.where(M, xu.double() - mx, torch.zeros((), dtype=torch.float64, device=x.device))
    yv = torch.where(M, yu.double() - my, torch.zeros((), dtype=torch.float64, device=x.device))
    sx = xv.sum(-1)
    sy = yv.sum(-1)
    sxy = (xv * yv).sum(-1)
    sx2 = (xv * xv).sum(-1)
    sy2 = (yv * yv).sum(-1)
    n = cnt.clamp(min=1)
    varx = torch.clamp(sx2 / n - (sx / n) ** 2, min=0.0)
    vary = torch.clamp(sy2 / n - (sy / n) ** 2, min=0.0)
    if as_cov:
        # formula_ops.cov 用 np.cov（ddof=1 样本协方差）
        cov = (sxy - (sx / n) * sy) / (n - 1).clamp(min=1)
        out = cov
        # 与 formula_ops 一致：任一序列零方差 → 0.0
        out = torch.where((varx < 1e-12) | (vary < 1e-12), torch.zeros((), device=x.device), out)
    else:
        cov = sxy / n - (sx / n) * (sy / n)
        denom = torch.sqrt(varx * vary)
        corr = torch.where(denom < 1e-12, torch.zeros((), dtype=torch.float64, device=x.device),
                           cov / denom.clamp(min=1e-12))
        out = corr
    out = torch.where(valid, out, torch.full((), float("nan"), dtype=torch.float64, device=x.device))
    return _roll_head_nan(out, w)


def _roll_ref(x: "torch.Tensor", d: int) -> "torch.Tensor":
    """delay(d)：头部 d 根 NaN。"""
    import torch

    if d <= 0:
        return x
    if d >= x.shape[-1]:
        return torch.full_like(x, float("nan"))
    out = torch.full_like(x, float("nan"))
    out[..., d:] = x[..., :-d]
    return out


# ── 栈式批量执行 ────────────────────────────────────────────────────

def _apply_step_unary(
    stack, ptr, rows, op_code: int, device,
) -> None:
    import torch

    x = stack[rows, ptr[rows] - 1]
    name = _OP_CODE_NAMES[op_code]
    eps_neg = torch.full((), -20.0, device=device)
    eps_pos = torch.full((), 20.0, device=device)
    if name == "abs":
        y = x.abs()
    elif name == "sign":
        # torch.sign(nan)=0，numpy np.sign(nan)=nan —— 必须显式 NaN 透传
        y = torch.where(torch.isnan(x), x, x.sign())
    elif name == "log":
        y = torch.where(x > 0, torch.log(x), torch.full((), float("nan"), dtype=torch.float64, device=device))
    elif name == "sqrt":
        y = torch.where(x >= 0, torch.sqrt(x), torch.full((), float("nan"), dtype=torch.float64, device=device))
    else:
        return
    stack[rows, ptr[rows] - 1] = y


def _apply_step_binary(
    stack, ptr, rows, op_code: int,
) -> None:
    import torch

    a = stack[rows, ptr[rows] - 2]
    b = stack[rows, ptr[rows] - 1]
    name = _OP_CODE_NAMES[op_code]
    if name == "add":
        y = a + b
    elif name == "sub":
        y = a - b
    elif name == "mul":
        y = a * b
    elif name == "div":
        y = torch.where(b.abs() > 1e-12, a / b, torch.zeros((), device=a.device))
    elif name == "pow":
        y = torch.pow(a.abs(), b) * a.sign()
        y = torch.where(torch.isfinite(y), y, torch.full((), float("nan"), dtype=torch.float64, device=a.device))
    elif name == "greater":
        y = (a > b).double()
    elif name == "less":
        y = (a < b).double()
    else:
        return
    stack[rows, ptr[rows] - 2] = y
    ptr[rows] -= 1


def _apply_step_rolling(
    stack, ptr, rows, op_code: int, params, device, mem_mb: float,
) -> None:
    """滚动/滞后算子：rows 内的程序按其参数分组执行。"""
    import torch

    name = _OP_CODE_NAMES[op_code]
    # 每程序栈顶
    x = stack[rows, ptr[rows] - 1]
    B = x.shape[-1]
    S = x.shape[-2]
    if name in ("ref", "delta"):
        for pu in torch.unique(params).tolist():
            sub = rows[params == pu]
            d = int(pu)
            xv = stack[sub, ptr[sub] - 1]
            if name == "ref":
                y = _roll_ref(xv, d)
            else:
                y = xv - _roll_ref(xv, d)
            stack[sub, ptr[sub] - 1] = y
        return
    # unfold 类算子：显存预算检查 → 超预算整组跳过（调用方已按 gpu_ok 划分，此处防御）
    w_max = int(params.max().item())
    if name in ("max", "min", "wma", "decay_linear", "ts_rank", "ts_argmax", "ts_argmin"):
        if not _unfold_budget_ok(len(rows), S, B, w_max, mem_mb):
            raise MemoryError(f"{name} w={w_max} 超显存预算")
    for pu in torch.unique(params).tolist():
        sub = rows[params == pu]
        w = int(pu)
        xv = stack[sub, ptr[sub] - 1]
        if name == "mean":
            y = _roll_mean(xv, w)
        elif name == "sum":
            y = _roll_sum(xv, w)
        elif name == "std":
            y = _roll_std_var(xv, w, do_sqrt=True)
        elif name == "var":
            y = _roll_std_var(xv, w, do_sqrt=False)
        elif name == "max":
            y = _roll_max_min(xv, w, is_max=True)
        elif name == "min":
            y = _roll_max_min(xv, w, is_max=False)
        elif name in ("wma", "decay_linear"):
            y = _roll_wma(xv, w)
        elif name == "ts_rank":
            y = _roll_ts_rank(xv, w)
        elif name == "ts_argmax":
            y = _roll_argmaxmin(xv, w, is_max=True)
        elif name == "ts_argmin":
            y = _roll_argmaxmin(xv, w, is_max=False)
        else:
            raise MemoryError(f"算子 {name} 无 GPU 实现")
        stack[sub, ptr[sub] - 1] = y


def _apply_step_pair(
    stack, ptr, rows, op_code: int, params, device, mem_mb: float,
) -> None:
    import torch

    name = _OP_CODE_NAMES[op_code]
    for pu in torch.unique(params).tolist():
        sub = rows[params == pu]
        w = int(pu)
        a = stack[sub, ptr[sub] - 2]
        b = stack[sub, ptr[sub] - 1]
        y = _roll_pair_corr(a, b, w, as_cov=(name == "cov"))
        stack[sub, ptr[sub] - 2] = y
        ptr[sub] -= 1


def stack_eval_batch(
    compiled: Dict[str, np.ndarray],
    fields: np.ndarray,
    device: str = "cuda",
    chunk: int = 64,
    mem_mb: float = 1200.0,
    mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """批量求值 GPU 子集程序 → (P, S, B) float32。

    fields: (F, S, B) float32；compiled 来自 compile_ast_batch。
    只执行 gpu_ok（且 mask=True）的程序；其余输出全 0。
    """
    import torch

    op_code = compiled["op_code"]
    param = compiled["param"]
    const = compiled["const"]
    field = compiled["field"]
    gpu_ok = compiled["gpu_ok"].astype(bool)
    if mask is not None:
        gpu_ok = gpu_ok & mask.astype(bool)
    max_stack = int(compiled["max_stack"])
    L = int(compiled["max_len"])
    P = op_code.shape[0]
    S, B = fields.shape[1], fields.shape[2]

    f = torch.as_tensor(fields, dtype=torch.float64, device=device)
    out = np.zeros((P, S, B), dtype=np.float64)

    idx_all = np.where(gpu_ok)[0]
    if len(idx_all) == 0:
        return out
    for c0 in range(0, len(idx_all), int(chunk)):
        idxs = idx_all[c0: c0 + int(chunk)]
        Pc = len(idxs)
        ok = torch.as_tensor(op_code[idxs], device=device)
        prm = torch.as_tensor(param[idxs], device=device)
        ct = torch.as_tensor(const[idxs], device=device).double()
        fd = torch.as_tensor(field[idxs], device=device).long()
        stack = torch.full(
            (Pc, max_stack + 1, S, B), float("nan"),
            dtype=torch.float64, device=device,
        )
        ptr = torch.zeros(Pc, dtype=torch.long, device=device)
        rows_all = torch.arange(Pc, device=device)

        for s in range(L):
            oc = ok[:, s]
            active = oc != _CODE_NOP
            if not active.any():
                continue
            rows = rows_all[active]

            # const
            m = oc == 1
            if m.any():
                r = rows_all[m]
                stack[r, ptr[r]] = ct[r, s].unsqueeze(-1).unsqueeze(-1)
                ptr[r] += 1
            # field
            m = oc == 2
            if m.any():
                r = rows_all[m]
                stack[r, ptr[r]] = f[fd[r, s]]
                ptr[r] += 1
            # 一元普通
            for code in [_OP_CODES[n] for n in _UNARY_OPS]:
                m = oc == code
                if m.any():
                    _apply_step_unary(stack, ptr, rows_all[m], code, device)
            # 二元普通
            for code in [_OP_CODES[n] for n in _BINARY_OPS]:
                m = oc == code
                if m.any():
                    _apply_step_binary(stack, ptr, rows_all[m], code)
            # 单序列参数算子
            for code in [_OP_CODES[n] for n in _UNARY_PARAM_OPS if n not in _CPU_ONLY_OPS]:
                m = oc == code
                if m.any():
                    r = rows_all[m]
                    _apply_step_rolling(stack, ptr, r, code, prm[r, s], device, mem_mb)
            # 双序列参数算子
            for code in [_OP_CODES[n] for n in _PAIR_OPS]:
                m = oc == code
                if m.any():
                    r = rows_all[m]
                    _apply_step_pair(stack, ptr, r, code, prm[r, s], device, mem_mb)

        # 结果 = 每程序栈顶
        top = ptr - 1
        res = stack[rows_all, top]  # (Pc, S, B)
        out[idxs] = res.cpu().numpy()
    return out


# ── 面板拼装 ────────────────────────────────────────────────────────

def assemble_panel(values: np.ndarray, lens: Sequence[int]) -> np.ndarray:
    """(P, S, B) 逐币截断拼接 → (P, n) float64（与 _stack_mine_panel.eval_fn 的 concat 对齐）。"""
    P = values.shape[0]
    cols = []
    for s, ln in enumerate(lens):
        cols.append(values[:, s, : int(ln)].astype(np.float64))
    return np.concatenate(cols, axis=1) if cols else np.zeros((P, 0))


def eval_panel_batch(
    asts: Sequence[dict],
    fields_per_symbol: Sequence[Dict[str, np.ndarray]],
    device: str = "cuda",
    chunk: int = 64,
    mem_mb: float = 1200.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """挖矿面板批量求值入口。

    返回 (values, gpu_ok)：values (P, n) float64 面板拼接值；
    gpu_ok (P,) bool —— False 表示该程序需 CPU 兜底（值未计算，全 0）。
    """
    lens = [int(len(next(iter(fd.values())))) if fd else 0 for fd in fields_per_symbol]
    Bmax = max(lens) if lens else 0
    if Bmax == 0 or not asts:
        return np.zeros((len(asts), 0)), np.zeros(len(asts), dtype=bool)
    field_order = sorted({k for fd in fields_per_symbol for k in fd.keys()})
    F = len(field_order)
    S = len(fields_per_symbol)
    fields = np.full((F, S, Bmax), np.nan, dtype=np.float64)
    for s, fd in enumerate(fields_per_symbol):
        for k, arr in fd.items():
            if k in field_order:
                fi = field_order.index(k)
                a = np.asarray(arr, dtype=np.float64).reshape(-1)[: Bmax]
                fields[fi, s, : len(a)] = a
    compiled = compile_ast_batch(asts, field_order)
    if compiled is None:
        return np.zeros((len(asts), sum(lens))), np.zeros(len(asts), dtype=bool)
    # 显存预算过滤：任一 unfold 算子超预算 → 该程序 CPU 兜底
    gpu_ok = compiled["gpu_ok"].astype(bool).copy()
    for p, up in enumerate(compiled["unfold_params"]):
        if not gpu_ok[p]:
            continue
        for _op, w in up:
            if not _unfold_budget_ok(int(chunk), S, Bmax, w, mem_mb):
                gpu_ok[p] = False
                break
    raw = stack_eval_batch(compiled, fields, device=device, chunk=chunk, mem_mb=mem_mb, mask=gpu_ok)
    values = assemble_panel(raw, lens)
    return values, gpu_ok


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def gpu_mem_ok(min_free_mb: float) -> bool:
    """[R9] 显存预算检查：空闲显存 >= min_free_mb 才允许 GPU 求值。"""
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        free, _total = torch.cuda.mem_get_info()
        return float(free) >= float(min_free_mb) * 1e6
    except Exception:
        return False


if __name__ == "__main__":
    # 自检：合成面板 + 随机 AST（挖矿形状）
    import random as _r

    rng = np.random.default_rng(0)
    S, B = 5, 2000
    fps = []
    for _s in range(S):
        c = np.abs(rng.normal(100, 2, B))
        fps.append({
            "close": c,
            "open": c + rng.normal(0, 0.5, B),
            "high": c + np.abs(rng.normal(1, 0.3, B)),
            "low": c - np.abs(rng.normal(1, 0.3, B)),
            "volume": np.abs(rng.normal(1e3, 2e2, B)) + 10,
            "returns": np.diff(c, prepend=c[0]) / c,
        })
    _trees = []
    for i in range(300):
        _trees.append({
            "op": "mean",
            "args": [
                {"op": "div", "args": [{"op": "delta", "args": [{"f": "close"}, {"c": 5}]},
                                       {"op": "std", "args": [{"f": "returns"}, {"c": 10}]}]},
                {"c": 10},
            ],
        })
    t0 = time.perf_counter()
    vals, ok = eval_panel_batch(_trees, fps, device="cuda")
    dt = time.perf_counter() - t0
    print(f"eval {len(_trees)} trees: {dt:.3f}s, gpu_ok={ok.sum()}/{len(_trees)}, shape={vals.shape}")
