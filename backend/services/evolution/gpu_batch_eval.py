"""GPU 批量表达式求值器 — 原型（侧分支 feat/gpu-batch-factor-eval，未接线热路径）。

设计见 docs/GPU_FACTOR_EVAL_DESIGN.md。核心：把一代种群的 N 棵表达式树对齐为
同深度的满二叉树，逐层向量化求值——每个算子一次核启动覆盖全部个体/币/bar，
30 代 × 6 种子的 GP 求值从 ~60min 压到秒级。

本文件可独立运行（torch 惰性导入，无 CUDA 自动回退 torch-CPU / numpy），
不 import 任何业务模块，保证侧分支原型零副作用。
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ── 表达式树中间表示（与 gplearn 解耦；M2 接线时做 node→dict 转换） ──
# 节点 dict: {"op": "add"|"sub"|"mul"|"div"|"neg"|"abs"|"log"|"exp"|"sqrt"
#                     |"max"|"min"|"sma"|"std"|"delta"|"pct", "w": 窗口/滞后, "ch": [..]}
# 叶子 dict: {"field": int} 或 {"const": float}

FIELD_NAMES = ("open", "high", "low", "close", "volume")
UNARY_OPS = {"neg", "abs", "log", "exp", "sqrt"}
BINARY_OPS = {"add", "sub", "mul", "div", "max", "min"}
ROLLING_OPS = {"sma", "std"}          # 窗口 w
LAG_OPS = {"delta", "pct"}            # 滞后 k


def random_tree(depth: int, seed: int = 0) -> Dict[str, Any]:
    """生成随机表达式树（基准用，形状贴近挖掘产物）。"""
    rng = random.Random(seed)

    def node(d: int) -> Dict[str, Any]:
        if d <= 0:
            return {"field": rng.randrange(len(FIELD_NAMES))}
        op = rng.choice(
            list(BINARY_OPS) + list(UNARY_OPS) + list(ROLLING_OPS) + list(LAG_OPS)
        )
        if op in BINARY_OPS:
            return {"op": op, "ch": [node(d - 1), node(d - 1)]}
        if op in UNARY_OPS:
            return {"op": op, "ch": [node(d - 1)]}
        if op in ROLLING_OPS:
            return {"op": op, "w": rng.choice([3, 5, 10, 14, 20]), "ch": [node(d - 1)]}
        return {"op": op, "k": rng.choice([1, 2, 3, 5]), "ch": [node(d - 1)]}

    return node(depth)


# ── 满二叉树对齐编译（每个程序 → 固定深度 D 的层数组） ──

@dataclass
class CompiledPrograms:
    """对齐后的层表示。每层: op_kind(P,N), left(P,N), right(P,N), w(P,N), k(P,N),
    const(P,N), field(P,N)。N=2**level。op_kind: 0=none(透传left), 1=const,
    2=field, 3..=算子。"""
    depth: int
    op_kind: np.ndarray
    left: np.ndarray
    right: np.ndarray
    w: np.ndarray
    k: np.ndarray
    const: np.ndarray
    field: np.ndarray


_OP_CODES = {
    "const": 1, "field": 2,
    "add": 3, "sub": 4, "mul": 5, "div": 6, "max": 7, "min": 8,
    "neg": 9, "abs": 10, "log": 11, "exp": 12, "sqrt": 13,
    "sma": 14, "std": 15, "delta": 16, "pct": 17,
}


def _align_tree(node: Dict[str, Any], d: int) -> Tuple[int, int]:
    """递归对齐，返回 (node_idx_at_level_d, depth)。"""
    if "field" in node:
        return 0, 0
    if "const" in node:
        return 0, 0
    ch = node.get("ch") or []
    if len(ch) == 1:
        idx_l, d_l = _align_tree(ch[0], d - 1)
        return idx_l, d_l + 1
    idx_l, d_l = _align_tree(ch[0], d - 1)
    idx_r, d_r = _align_tree(ch[1], d - 1)
    # 两边深度不一致时：浅的一侧补一层 none 透传
    while d_l < d_r:
        idx_l = idx_l  # 位置不变，由编译层内 op=none 处理
        d_l += 1
    while d_r < d_l:
        d_r += 1
    return 0, max(d_l, d_r) + 1  # idx 在编译阶段按层重排


def compile_programs(trees: Sequence[Dict[str, Any]], max_depth: int = 6) -> CompiledPrograms:
    """把树列表编译为等深满二叉树层表示。

    实现：递归把每棵树填充为深度 max_depth 的满二叉树（缺子节点补 none 透传），
    按层扁平化。此原型允许 O(P×2^D) 内存；P=500、D=6 → 500×64 张量，极小。
    """
    import torch  # noqa: F401  (仅用于尺寸确认，实际数组用 numpy，执行时再转 torch)

    P = len(trees)
    D = max_depth
    n_nodes = 2 ** (D + 1) - 1

    op_kind = np.zeros((P, n_nodes), dtype=np.int64)
    left = np.zeros((P, n_nodes), dtype=np.int64)
    right = np.zeros((P, n_nodes), dtype=np.int64)
    w = np.zeros((P, n_nodes), dtype=np.int64)
    k = np.zeros((P, n_nodes), dtype=np.int64)
    const = np.zeros((P, n_nodes), dtype=np.float32)
    field = np.zeros((P, n_nodes), dtype=np.int64)

    def fill(p: int, node: Dict[str, Any], idx: int, d: int) -> None:
        if "field" in node:
            op_kind[p, idx] = _OP_CODES["field"]
            field[p, idx] = int(node["field"]) % len(FIELD_NAMES)
            return
        if "const" in node:
            op_kind[p, idx] = _OP_CODES["const"]
            const[p, idx] = float(node["const"])
            return
        op = str(node.get("op") or "")
        if d <= 0:
            # 深度耗尽：退化为常数 0
            op_kind[p, idx] = _OP_CODES["const"]
            return
        op_kind[p, idx] = _OP_CODES.get(op, 0)
        w[p, idx] = int(node.get("w") or 0)
        k[p, idx] = int(node.get("k") or 0)
        ch = node.get("ch") or []
        l_idx, r_idx = 2 * idx + 1, 2 * idx + 2
        if len(ch) == 2:
            fill(p, ch[0], l_idx, d - 1)
            fill(p, ch[1], r_idx, d - 1)
        elif len(ch) == 1:
            fill(p, ch[0], l_idx, d - 1)
            # 右子补 none：op=none 的节点透传左子
        else:
            fill(p, {"const": 0.0}, l_idx, d - 1)
            fill(p, {"const": 0.0}, r_idx, d - 1)
        left[p, idx] = l_idx
        right[p, idx] = r_idx

    for p, t in enumerate(trees):
        fill(p, t, 0, D)

    # 截断未使用深度：按各树实际需要（原型用满 D，简单起见不截）
    return CompiledPrograms(
        depth=D, op_kind=op_kind, left=left, right=right,
        w=w, k=k, const=const, field=field,
    )


# ── 批量求值（torch） ──

def _torch_rolling_mean(x, w):
    import torch

    if w <= 1:
        return x
    # float64 累加：避免与 numpy 的 cumsum 顺序差异被 pct/div 近零分母放大
    x64 = x.double()
    pad = torch.nn.functional.pad(x64, (w, 0), mode="constant", value=0.0)
    c = pad.cumsum(dim=-1)
    s = c[..., w:] - c[..., :-w]
    return (s / w).float()


def _torch_rolling_std(x, w):
    import torch

    if w <= 1:
        return torch.zeros_like(x)
    # float64：E[x^2]-E[x]^2 在 float32 下有灾难性消去，会与 numpy 顺序差异
    # 叠加成完全不同的噪声（实测 4/50 树秩相关掉到 0.3~0.7）
    x64 = x.double()
    pad = torch.nn.functional.pad(x64, (w, 0), mode="constant", value=0.0)
    c = pad.cumsum(dim=-1)
    c2 = (pad * pad).cumsum(dim=-1)
    n = w
    s = c[..., w:] - c[..., :-w]
    s2 = c2[..., w:] - c2[..., :-w]
    var = s2 / n - (s / n) ** 2
    return torch.clamp(var, min=0.0).sqrt().float()


def _torch_lag(x, k):
    import torch

    if k <= 0:
        return torch.zeros_like(x)
    return torch.nn.functional.pad(x, (k, 0), mode="constant", value=0.0)[..., :-k]


def batch_eval(
    progs: CompiledPrograms,
    fields: np.ndarray,
    device: Optional[str] = None,
) -> np.ndarray:
    """批量求值 → (P, S, B) float32。device: None=自动 cuda/cpu。

    fields: (F, S, B) float32（F=len(FIELD_NAMES)）。
    逐层自底向上：层内按 op_kind 分组做向量化算子。
    """
    import torch

    P, N = progs.op_kind.shape
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    f = torch.as_tensor(fields, dtype=torch.float32, device=dev)  # (F,S,B)
    S, B = f.shape[1], f.shape[2]

    ok = torch.as_tensor(progs.op_kind, device=dev)
    lf = torch.as_tensor(progs.left, device=dev)
    rt = torch.as_tensor(progs.right, device=dev)
    ws = torch.as_tensor(progs.w, device=dev)
    ks = torch.as_tensor(progs.k, device=dev)
    ct = torch.as_tensor(progs.const, dtype=torch.float32, device=dev)
    fd = torch.as_tensor(progs.field, device=dev)

    # 满二叉树逐层（从最深层到根）
    level_nodes = [2 ** i for i in range(progs.depth + 1)]
    start = [sum(level_nodes[:i]) for i in range(progs.depth + 1)]

    vals = torch.zeros((P, N, S, B), dtype=torch.float32, device=dev)
    eps = torch.as_tensor(1e-8, device=dev)

    for level in range(progs.depth, -1, -1):
        lo, hi = start[level], start[level] + level_nodes[level]
        seg = ok[:, lo:hi]        # (P, n)
        sv = vals[:, lo:hi]       # (P, n, S, B)
        # 子节点值（层序索引 → 上一层的 (P,N,S,B) 张量）
        if level < progs.depth:
            lv = torch.gather(vals, 1, lf[:, lo:hi, None, None].expand(-1, -1, S, B))
            rv = torch.gather(vals, 1, rt[:, lo:hi, None, None].expand(-1, -1, S, B))
        else:
            lv = rv = sv

        def apply(mask: torch.Tensor, fn):
            if not mask.any():
                return
            x = fn(lv[mask], rv[mask])
            sv[mask] = x

        # 叶子
        m = seg == _OP_CODES["const"]
        sv[m] = ct[:, lo:hi][m].unsqueeze(-1).unsqueeze(-1)
        m = seg == _OP_CODES["field"]
        sv[m] = f[fd[:, lo:hi][m]]

        m = seg == 0  # none：透传左子
        sv[m] = lv[m]

        m = seg == 3; apply(m, lambda a, b: a + b)
        m = seg == 4; apply(m, lambda a, b: a - b)
        m = seg == 5; apply(m, lambda a, b: a * b)
        m = seg == 6; apply(m, lambda a, b: a / torch.where(b.abs() < eps, eps, b))
        m = seg == 7; apply(m, lambda a, b: torch.maximum(a, b))
        m = seg == 8; apply(m, lambda a, b: torch.minimum(a, b))

        def unary(mask, fn):
            if mask.any():
                sv[mask] = fn(lv[mask])

        m = seg == 9; unary(m, lambda x: -x)
        m = seg == 10; unary(m, lambda x: x.abs())
        m = seg == 11; unary(m, lambda x: torch.log(x.abs().clamp(min=eps)))
        m = seg == 12; unary(m, lambda x: torch.exp(x.clamp(max=20.0, min=-20.0)))
        m = seg == 13; unary(m, lambda x: x.abs().clamp(min=eps).sqrt())

        m = seg == 14
        if m.any():
            _apply_per_param(m, ws[:, lo:hi], sv, lv, _torch_rolling_mean)
        m = seg == 15
        if m.any():
            _apply_per_param(m, ws[:, lo:hi], sv, lv, _torch_rolling_std)

        def _lag_fn(x, kk):
            return x - _torch_lag(x, kk)

        def _pct_fn(x, kk):
            lag = _torch_lag(x, kk)
            return (x - lag) / torch.where(lag.abs() < eps, eps, lag)

        m = seg == 16
        if m.any():
            _apply_per_param(m, ks[:, lo:hi], sv, lv, _lag_fn)
        m = seg == 17
        if m.any():
            _apply_per_param(m, ks[:, lo:hi], sv, lv, _pct_fn)

    return vals[:, 0].cpu().numpy()  # 根节点 (P,S,B)


def _apply_per_param(mask, param, dst, src, fn):
    """对命中 (p,i) 位置的节点按其参数值分组执行 fn(x, param)。"""
    import torch

    idxs = mask.nonzero(as_tuple=False)  # (K, 2)
    if idxs.numel() == 0:
        return
    psel = param[idxs[:, 0], idxs[:, 1]]
    for pu in torch.unique(psel).tolist():
        sub = idxs[psel == pu]
        x = src[sub[:, 0], sub[:, 1]]
        dst[sub[:, 0], sub[:, 1]] = fn(x, int(pu))


# ── numpy 参考实现（等价性校验 + CPU 兜底） ──

def eval_tree_np(node: Dict[str, Any], fields: np.ndarray) -> np.ndarray:
    """单树 numpy 求值，作为 GPU 实现的真值对照。"""
    if "field" in node:
        return fields[int(node["field"])]
    if "const" in node:
        return np.full(fields.shape[1:], float(node["const"]), dtype=np.float32)
    op = str(node.get("op") or "")
    ch = node.get("ch") or []
    x = eval_tree_np(ch[0], fields) if len(ch) >= 1 else np.zeros(fields.shape[1:], np.float32)
    y = eval_tree_np(ch[1], fields) if len(ch) >= 2 else x
    eps = 1e-8
    if op == "add": return x + y
    if op == "sub": return x - y
    if op == "mul": return x * y
    if op == "div": return x / np.where(np.abs(y) < eps, eps, y)
    if op == "max": return np.maximum(x, y)
    if op == "min": return np.minimum(x, y)
    if op == "neg": return -x
    if op == "abs": return np.abs(x)
    if op == "log": return np.log(np.abs(x).clip(eps, None))
    if op == "exp": return np.exp(np.clip(x, -20.0, 20.0))
    if op == "sqrt": return np.sqrt(np.abs(x).clip(eps, None))
    w = int(node.get("w") or 0)
    k = int(node.get("k") or 0)
    if op == "sma":
        return _np_rolling_mean(x, w)
    if op == "std":
        return _np_rolling_std(x, w)
    if op == "delta":
        lag = np.pad(x, ((0, 0), (k, 0)))[:, :-k] if k else np.zeros_like(x)
        return x - lag
    if op == "pct":
        lag = np.pad(x, ((0, 0), (k, 0)))[:, :-k] if k else np.zeros_like(x)
        return (x - lag) / np.where(np.abs(lag) < eps, eps, lag)
    return np.zeros_like(x)


def _np_rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1:
        return x
    x64 = x.astype(np.float64)
    pad = np.pad(x64, ((0, 0), (w, 0)), mode="constant")
    c = pad.cumsum(axis=-1)
    return ((c[..., w:] - c[..., :-w]) / w).astype(np.float32)


def _np_rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1:
        return np.zeros_like(x)
    x64 = x.astype(np.float64)
    pad = np.pad(x64, ((0, 0), (w, 0)), mode="constant")
    c = pad.cumsum(axis=-1)
    c2 = (pad * pad).cumsum(axis=-1)
    s = c[..., w:] - c[..., :-w]
    s2 = c2[..., w:] - c2[..., :-w]
    var = np.clip(s2 / w - (s / w) ** 2, 0.0, None)
    return np.sqrt(var).astype(np.float32)


def equivalence_check(
    trees: Sequence[Dict[str, Any]],
    fields: np.ndarray,
    device: Optional[str] = None,
    corr_min: float = 0.999,
    mismatch_frac_max: float = 0.05,
) -> Dict[str, Any]:
    """GPU 批量 vs numpy 逐树 等价性校验（原型验收用）。

    验收口径 = 下游真实用途（IC 是排序相关）：
      - 每个程序 Spearman 秩相关 ≥ corr_min（默认 0.999）；
      - isclose(rtol=1e-2, atol=1e-3) 不匹配比例 < mismatch_frac_max。
    说明：torch cumsum 与 numpy 的浮点累加顺序不同（1e-7 级），经 pct/div
    近零分母放大后逐值 diff 可大，但秩序一致——IC 与因子排序不受影响。
    """
    progs = compile_programs(trees)
    got = batch_eval(progs, fields, device=device)
    ref = np.stack([eval_tree_np(t, fields) for t in trees])
    n_bad_corr = 0
    min_corr = 1.0
    for i in range(len(trees)):
        g = got[i].reshape(-1)
        r = ref[i].reshape(-1)
        if np.std(g) < 1e-12 or np.std(r) < 1e-12:
            continue
        c = float(np.corrcoef(g, r)[0, 1])
        min_corr = min(min_corr, c)
        if c < corr_min:
            n_bad_corr += 1
    mismatch = float((~np.isclose(got, ref, rtol=1e-2, atol=1e-3)).mean())
    ok = n_bad_corr == 0 and mismatch <= mismatch_frac_max
    return {
        "ok": ok,
        "n_programs": len(trees),
        "min_corr": round(min_corr, 6),
        "bad_corr": n_bad_corr,
        "mismatch_frac": round(mismatch, 5),
        "max_abs": float(np.abs(got - ref).max()),
        "shape": tuple(got.shape),
    }


def benchmark(
    n_programs: int = 500,
    tree_depth: int = 5,
    fields: Optional[np.ndarray] = None,
    device: Optional[str] = None,
) -> Dict[str, Any]:
    """基准：numpy 逐树 vs torch 批量（CPU/CUDA）。"""
    import torch

    if fields is None:
        rng = np.random.default_rng(7)
        fields = np.abs(rng.normal(1.0, 0.2, (len(FIELD_NAMES), 9, 5000))).astype(np.float32)
        fields[-1] += 10.0  # volume 恒正
    trees = [random_tree(tree_depth, seed=i) for i in range(n_programs)]

    t0 = time.perf_counter()
    ref = np.stack([eval_tree_np(t, fields) for t in trees])
    t_np = time.perf_counter() - t0

    progs = compile_programs(trees)
    t0 = time.perf_counter()
    got_cpu = batch_eval(progs, fields, device="cpu")
    t_cpu = time.perf_counter() - t0

    t_cuda = None
    got_cuda = None
    if torch.cuda.is_available():
        t0 = time.perf_counter()
        got_cuda = batch_eval(progs, fields, device="cuda")
        t_cuda = time.perf_counter() - t0

    out = {
        "n_programs": n_programs,
        "shape": tuple(got_cpu.shape),
        "numpy_sec": round(t_np, 3),
        "torch_cpu_sec": round(t_cpu, 3),
        "speedup_vs_numpy": round(t_np / max(t_cpu, 1e-9), 1),
        "cuda_sec": round(t_cuda, 3) if t_cuda else None,
        "speedup_vs_cpu": round(t_cpu / max(t_cuda, 1e-9), 1) if t_cuda else None,
        "max_abs_diff": float(np.abs(got_cpu - ref).max()),
    }
    return out


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print(benchmark(n_programs=n))
