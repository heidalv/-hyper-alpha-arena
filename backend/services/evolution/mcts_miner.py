"""
MCTS 因子挖掘器（阶段2 S2-12）。

把因子表达式搜索建模为蒙特卡洛树搜索：
- 节点 = 因子 AST（表达式），边 = 一次局部变换（参数变异 / 算子替换 / 子树替换 / 根包装）
- UCT 选择：Q(v)/N(v) + c·sqrt(ln(N(parent))/N(v)) 平衡利用高分支与探索稀疏分支
- 短板扩展：以活跃集中 IC 最弱因子为根（weak seeds），定向改进短板而非全域随机
- FSA 因子敏感性分析：对窗口参数做 ±50% 三档扫描，
  a) 敏感度超阈值的候选视为"参数不稳因子"直接拒绝（防过拟合某窗口档位）
  b) 高敏感参数路径作为扩展方向偏好（向敏感参数做更细的窗口变异）
- CoE 进化链：树中"fitness 优于父节点"的边构成进化链，返回调用方落库
  （factor_evolution_log action=mcts_chain），保留因子血缘可追溯
- 宏微分离：micro(1m/5m/15m) / mid / macro(4h/8h/1d) 使用不同窗口档位、
  最大深度与复杂度惩罚，避免跨时间尺度噪声污染

接入点：factor_evolution_loop._mine_candidates（与 GPMiner 并列的第二种挖掘器）。
"""
from __future__ import annotations

import copy
import logging
import math
import os

# 与 gp_miner 相同的 loky worker 单线程 BLAS 约束（32 进程并行评估前提）
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import numpy as np
from joblib import Parallel, delayed

from backend.services.factor_engine.expr.audit import audit
from backend.services.factor_engine.expr.ops import OP_REGISTRY
from backend.services.factor_engine.expr.parser import FactorExpr, parse

logger = logging.getLogger(__name__)

_NEG_INF = float("-inf")

# 窗口/标量参数类算子：最后一个参数强制为常量（窗口长度或 scale 系数）
_WINDOW_OPS = frozenset({
    "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank", "delta",
    "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin", "scale",
    "corr", "cov", "ts_corr",
})
# 单目算子（arity=1，可用于根包装/算子替换）
_UNARY_OPS = [op for op, (a, _) in OP_REGISTRY.items() if a == 1]
_BINARY_OPS = [op for op, (a, _) in OP_REGISTRY.items() if a == 2]
_TERNARY_OPS = [op for op, (a, _) in OP_REGISTRY.items() if a == 3]

# ─────────────────────────────────────────────
#  宏微分离：按周期档位选择窗口值/深度/惩罚
# ─────────────────────────────────────────────
_SCALE_PROFILES: dict[str, dict] = {
    # micro: 1m/5m/15m 短周期 → 短窗口 + 浅深度 + 强复杂度惩罚（噪声多，防过拟合）
    "micro": {
        "windows": [3, 5, 10, 20],
        "max_depth": 5,
        "rollout_steps": 2,
        "lambda_complexity": 2e-3,
    },
    # mid: 30m/1h/2h 中周期
    "mid": {
        "windows": [3, 5, 10, 20, 50],
        "max_depth": 6,
        "rollout_steps": 2,
        "lambda_complexity": 1e-3,
    },
    # macro: 4h/8h/1d 长周期 → 长窗口 + 允许更深 + 模拟步数多
    "macro": {
        "windows": [5, 10, 20, 50],
        "max_depth": 6,
        "rollout_steps": 3,
        "lambda_complexity": 1e-3,
    },
}
_CONST_VALUES = [1, 2, 3, 5, 10, 20]


def scale_for_period(period: Optional[str]) -> str:
    """按周期映射宏微尺度（宏微分离入口）。"""
    p = (period or "").lower()
    if p in ("1m", "5m", "15m"):
        return "micro"
    if p in ("4h", "8h", "1d"):
        return "macro"
    return "mid"


@dataclass
class MCTSConfig:
    """MCTS 挖掘配置（S2-12）。"""
    n_iterations: int = 300          # 每棵树的 UCT 迭代预算
    n_children: int = 5              # 每次扩展生成的子节点数
    n_roots: int = 3                 # 树根数量（短板种子 + 随机根补齐）
    uct_c: float = 1.4               # UCT 探索系数
    max_depth: int = 6               # 树深上限（宏微分离 profile 可覆盖）
    rollout_steps: int = 2           # 模拟深度（宏微分离 profile 可覆盖）
    n_weak_seeds: int = 3            # 短板种子数（活跃集中 |IC| 最低者）
    min_ic_keep: float = 0.02        # 收集节点的 |IC| 下限
    top_k: int = 30                  # 准入尝试 top 候选数
    lambda_complexity: float = 1e-3  # 复杂度惩罚（宏微分离 profile 可覆盖）
    lambda_corr: float = 0.05        # 与短板根最大相关惩罚
    min_samples: int = 50            # 有效样本下限
    fsa_reject_threshold: float = 0.8  # FSA 敏感度阈值：超此值视为参数不稳拒绝
    fsa_scan_weights: Tuple[float, ...] = (0.5, 1.0, 1.5)  # 窗口扫描倍数
    windows: Tuple[int, ...] = (3, 5, 10, 20, 50)  # 窗口档位（宏微分离 profile 覆盖）
    max_workers: int = 0             # 0 = min(8, cpu)（扩展批量评估用）
    scale: str = "mid"               # micro | mid | macro（宏微分离）

    def __post_init__(self) -> None:
        prof = _SCALE_PROFILES.get(self.scale, _SCALE_PROFILES["mid"])
        self.max_depth = int(prof["max_depth"])
        self.rollout_steps = int(prof["rollout_steps"])
        self.lambda_complexity = float(prof["lambda_complexity"])
        self.windows = tuple(int(w) for w in prof["windows"])


@dataclass
class MctsNode:
    """MCTS 树节点：因子 AST + 搜索统计。"""
    ast: dict
    parent: Optional["MctsNode"] = None
    children: List["MctsNode"] = field(default_factory=list)
    visits: int = 0
    value_sum: float = 0.0
    ic: float = 0.0                  # 本节点 IC（缓存评估结果）
    fitness: float = _NEG_INF        # 本节点适应度（|IC| − λ1·复杂度 − λ2·相关）
    depth: int = 0

    @property
    def value(self) -> float:
        return self.value_sum / self.visits if self.visits > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
#  AST 工具（与 gp_miner 同构，独立实现避免跨模块耦合）
# ═══════════════════════════════════════════════════════════════

def _count_nodes(ast: dict) -> int:
    if "args" not in ast:
        return 1
    return 1 + sum(_count_nodes(a) for a in ast["args"])


def _depth(ast: dict) -> int:
    if "args" not in ast or not ast["args"]:
        return 1
    return 1 + max(_depth(a) for a in ast["args"])


def _collect_nodes(ast: dict, path: Tuple[int, ...] = ()) -> List[Tuple[Tuple[int, ...], dict]]:
    nodes = [(path, ast)]
    if "args" in ast:
        for i, child in enumerate(ast["args"]):
            nodes.extend(_collect_nodes(child, path + (i,)))
    return nodes


def _set_at(ast: dict, path: Tuple[int, ...], new_node: dict) -> None:
    node = ast
    for i in path[:-1]:
        node = node["args"][i]
    node["args"][path[-1]] = new_node


def _window_param_paths(ast: dict) -> List[Tuple[Tuple[int, ...], float]]:
    """FSA：返回所有"窗口/标量常量参数"的 (路径, 当前值)。"""
    paths: List[Tuple[Tuple[int, ...], float]] = []

    def walk(node: dict, path: Tuple[int, ...]) -> None:
        if "op" in node:
            op = str(node["op"])
            arity, _ = OP_REGISTRY.get(op, (0, None))
            if op in _WINDOW_OPS and arity >= 2 and node.get("args"):
                last = node["args"][-1]
                if isinstance(last, dict) and "c" in last:
                    v = float(last["c"])
                    if abs(v) > 1e-9:
                        paths.append((path + (len(node["args"]) - 1,), v))
            for i, child in enumerate(node.get("args", [])):
                walk(child, path + (i,))

    walk(ast, ())
    return paths


# ═══════════════════════════════════════════════════════════════
#  FSA — 因子敏感性分析（Factor Sensitivity Analysis）
# ═══════════════════════════════════════════════════════════════

def sensitivity_scan(
    ast: dict,
    factor_value_fn: Callable[[dict], np.ndarray],
    target: np.ndarray,
    min_samples: int = 50,
    weights: Tuple[float, ...] = (0.5, 1.0, 1.5),
) -> dict:
    """对 AST 中所有窗口参数做 ±50% 三档扫描，返回各参数敏感度。

    敏感度 = std(|IC|) / mean(|IC|)：> fsa_reject_threshold 视为参数不稳。
    同时返回扫描明细供扩展方向偏好使用（高敏感参数优先做窗口细分变异）。
    """
    paths = _window_param_paths(ast)
    tgt = np.asarray(target, dtype=float)
    params: List[dict] = []
    for path, base in paths:
        ics: List[float] = []
        for factor in weights:
            if factor == 1.0:
                trial = copy.deepcopy(ast)
            else:
                trial = copy.deepcopy(ast)
                _set_at(trial, path, {"c": float(base * factor)})
            try:
                fv = np.asarray(factor_value_fn({"expr": parse(trial)}), dtype=float)
                if fv.ndim == 0:
                    fv = np.full_like(tgt, float(fv))
                if fv.shape != tgt.shape:
                    continue
                m = np.isfinite(fv) & np.isfinite(tgt)
                if m.sum() < min_samples:
                    continue
                if np.std(fv[m]) < 1e-12:
                    continue
                ic = abs(float(np.corrcoef(fv[m], tgt[m])[0, 1]))
                if np.isfinite(ic):
                    ics.append(ic)
            except Exception:
                continue
        if len(ics) >= 2 and np.mean(ics) > 1e-12:
            sens = float(np.std(ics) / np.mean(ics))
        else:
            sens = 0.0
        params.append({
            "path": list(path), "base": float(base),
            "ic_values": ics, "sensitivity": sens,
        })
    max_sens = max((p["sensitivity"] for p in params), default=0.0)
    return {"n_params": len(params), "max_sensitivity": float(max_sens), "params": params}


# ═══════════════════════════════════════════════════════════════
#  loky worker 评估核心（模块级，可序列化）
# ═══════════════════════════════════════════════════════════════

def _mcts_fitness_core(ast: dict, state: dict) -> Tuple[float, float]:
    """模块级评估：返回 (ic, fitness)。fitness = |IC| − λ1×复杂度 − λ2×最大相关。"""
    factor_value_fn = state["factor_value_fn"]
    target = state["target"]
    try:
        fv = np.asarray(factor_value_fn({"expr": parse(ast)}), dtype=float)
    except Exception:
        return 0.0, _NEG_INF
    if fv.ndim == 0:
        fv = np.full_like(target, float(fv))
    if fv.shape != target.shape:
        return 0.0, _NEG_INF
    mask = np.isfinite(fv) & np.isfinite(target)
    if mask.sum() < state["min_samples"]:
        return 0.0, _NEG_INF
    if np.std(fv[mask]) < 1e-12:
        return 0.0, _NEG_INF
    ic = float(np.corrcoef(fv[mask], target[mask])[0, 1])
    if not np.isfinite(ic):
        return 0.0, _NEG_INF
    penalty_c = state["lambda_complexity"] * _count_nodes(ast)
    corr_pen = 0.0
    if state.get("root_asts"):
        max_corr = 0.0
        for r_ast in state["root_asts"]:
            try:
                e_fv = np.asarray(factor_value_fn({"expr": parse(r_ast)}), dtype=float)
            except Exception:
                continue
            if e_fv.ndim == 0:
                e_fv = np.full_like(target, float(e_fv))
            m2 = np.isfinite(fv) & np.isfinite(e_fv)
            if m2.sum() < 10:
                continue
            c = abs(float(np.corrcoef(fv[m2], e_fv[m2])[0, 1]))
            if np.isfinite(c):
                max_corr = max(max_corr, c)
        corr_pen = state["lambda_corr"] * max_corr
    return ic, float(abs(ic) - penalty_c - corr_pen)


# ═══════════════════════════════════════════════════════════════
#  MCTS 挖掘器
# ═══════════════════════════════════════════════════════════════

class MctsMiner:
    """
    MCTS 因子挖掘器。

    用法（与 GPMiner 同构）：
        miner = MctsMiner(fields, factor_value_fn, target, pool, config, weak_seeds=...)
        admitted, chains = miner.mine()   # admitted: [(expr, contribution), ...]
    """

    def __init__(
        self,
        fields: List[str],
        factor_value_fn: Callable[[dict], np.ndarray],
        target: np.ndarray,
        pool,
        config: Optional[MCTSConfig] = None,
        weak_seeds: Optional[List[dict]] = None,
    ):
        self.fields = [f for f in fields if f]
        self.factor_value_fn = factor_value_fn
        self.target = np.asarray(target, dtype=float)
        self.pool = pool
        self.config = config or MCTSConfig()
        # 短板种子：活跃集中 |IC| 最低因子的 AST（定向改进短板）
        self.weak_seeds = weak_seeds or []
        self._seen_ast: set = set()
        self._par = None

    def close(self) -> None:
        """释放并行评估进程池。"""
        par = getattr(self, "_par", None)
        if par is not None:
            try:
                par._terminate_backend()
            except Exception:
                pass
            self._par = None

    # ─────────────────────────── 对外入口 ───────────────────────────

    def mine(
        self,
        pool_factor_matrix: Optional[np.ndarray] = None,
    ) -> Tuple[List[Tuple[FactorExpr, float]], List[dict]]:
        """多根 UCT 搜索 → 汇总 → FSA 过滤 → AlphaPool 池感知准入。

        返回 (admitted, chains)：
        - admitted: [(expr, contribution), ...]（与 GPMiner.mine 同构）
        - chains: CoE 进化链 [{parent_ast, child_ast, parent_fitness, child_fitness}, ...]
        """
        roots = self._build_roots()
        if not roots:
            logger.warning("[MctsMiner] 无可用树根（短板种子与随机根均失败）")
            return [], []
        self._root_asts = [copy.deepcopy(r) for r in roots]
        self._root_ic = {
            str(r): abs(self._eval_ast(r)[0]) for r in roots
        }

        all_nodes: List[MctsNode] = []
        all_chains: List[dict] = []
        for root_ast in roots:
            nodes, chains = self._run_tree(root_ast)
            all_nodes.extend(nodes)
            all_chains.extend(chains)

        # 去重 + 按 fitness 排序取 top_k
        seen: set = set()
        uniq: List[MctsNode] = []
        for n in all_nodes:
            key = str(n.ast)
            if key not in seen:
                seen.add(key)
                uniq.append(n)
        uniq.sort(key=lambda n: n.fitness, reverse=True)
        top = [n for n in uniq if abs(n.ic) >= self.config.min_ic_keep]
        top = top[: self.config.top_k]
        logger.info(
            f"[MctsMiner] UCT 搜索完成: {len(roots)} 根, 节点 {len(uniq)}, "
            f"达标候选 {len(top)}, 进化链 {len(all_chains)}"
        )

        # FSA：过滤高敏感（参数不稳）候选
        kept: List[MctsNode] = []
        for node in top:
            try:
                sa = sensitivity_scan(
                    node.ast, self.factor_value_fn, self.target,
                    min_samples=self.config.min_samples,
                    weights=self.config.fsa_scan_weights,
                )
            except Exception:
                sa = {"max_sensitivity": 0.0}
            if sa["max_sensitivity"] > self.config.fsa_reject_threshold:
                continue
            kept.append(node)
        if len(top) and len(kept) < len(top):
            logger.info(
                f"[MctsMiner] FSA 过滤: {len(top) - len(kept)} 个参数不稳候选被拒"
            )

        self.close()

        # 池感知准入（复用 AlphaPool.try_admit）
        admitted: List[Tuple[FactorExpr, float]] = []
        for node in kept:
            try:
                expr = parse(node.ast)
                fv = self.factor_value_fn({"expr": expr})
                fv_arr = np.asarray(fv, dtype=float)
                if fv_arr.ndim == 0:
                    fv_arr = np.full_like(self.target, float(fv_arr))
                ok, contribution = self.pool.try_admit(
                    expr, fv_arr, self.target,
                    pool_factor_matrix=pool_factor_matrix,
                )
            except Exception:
                continue
            if ok:
                admitted.append((expr, contribution))
        logger.info(f"[MctsMiner] 池准入: {len(admitted)}/{len(kept)} 命中")
        return admitted, all_chains

    # ─────────────────────────── 树根构建（短板扩展） ───────────────────────────

    def _build_roots(self) -> List[dict]:
        """树根 = 短板种子（weak_seeds）+ 随机根补齐到 n_roots。"""
        rng = np.random.default_rng()
        roots: List[dict] = []
        for ast in self.weak_seeds:
            if not isinstance(ast, dict):
                continue
            try:
                if audit(ast).ok and _depth(ast) <= self.config.max_depth:
                    roots.append(copy.deepcopy(ast))
            except Exception:
                continue
        attempts = 0
        while len(roots) < self.config.n_roots and attempts < self.config.n_roots * 8:
            attempts += 1
            ast = self._random_ast(rng, depth=0)
            if ast is None or not audit(ast).ok:
                continue
            key = str(ast)
            if key in self._seen_ast:
                continue
            self._seen_ast.add(key)
            roots.append(ast)
        return roots

    # ─────────────────────────── 单树 UCT 迭代 ───────────────────────────

    def _run_tree(self, root_ast: dict) -> Tuple[List[MctsNode], List[dict]]:
        """单根完整 UCT 搜索。返回 (全部节点, 改进进化链)。"""
        root = MctsNode(ast=root_ast, depth=0)
        root.ic, root.fitness = self._eval_ast(root_ast)
        root_ic_abs = abs(root.ic)
        rng = np.random.default_rng()
        self._seen_ast.add(str(root_ast))

        for _ in range(self.config.n_iterations):
            node = self._uct_select(root, rng)
            if not node.children and node.depth < self.config.max_depth:
                self._expand(node, rng)
                if not node.children:
                    continue
                node = node.children[0]
            reward = self._rollout(node, root_ic_abs, rng)
            self._backprop(node, reward)

        all_nodes: List[MctsNode] = []

        def collect(n: MctsNode) -> None:
            all_nodes.append(n)
            for c in n.children:
                collect(c)

        collect(root)

        # CoE：fitness 优于父节点且为正的边构成进化链
        chains: List[dict] = []
        for n in all_nodes:
            if (
                n.parent is not None
                and n.fitness > n.parent.fitness
                and n.fitness > 0
            ):
                chains.append({
                    "parent_ast": copy.deepcopy(n.parent.ast),
                    "child_ast": copy.deepcopy(n.ast),
                    "parent_fitness": float(n.parent.fitness),
                    "child_fitness": float(n.fitness),
                    "child_ic": float(n.ic),
                })
        return all_nodes, chains

    def _uct_select(self, node: MctsNode, rng: np.random.Generator) -> MctsNode:
        """UCT 选择：Q/N + c·sqrt(ln(Np)/N)，未访问孩子优先（随机序打破平局）。"""
        cur = node
        while cur.children:
            children = list(cur.children)
            rng.shuffle(children)
            total_parent = max(1, sum(c.visits for c in children))
            best = None
            best_uct = _NEG_INF
            for c in children:
                if c.visits == 0:
                    uct = float("inf")
                else:
                    q = c.value_sum / c.visits
                    uct = q + self.config.uct_c * math.sqrt(
                        math.log(total_parent) / c.visits
                    )
                if uct > best_uct:
                    best_uct = uct
                    best = c
            if best is None:
                break
            cur = best
        return cur

    def _expand(self, node: MctsNode, rng: np.random.Generator) -> None:
        """扩展：生成 n_children 个经审计的去重子节点（批量并行评估）。"""
        children_ast: List[dict] = []
        attempts = 0
        while len(children_ast) < self.config.n_children and attempts < self.config.n_children * 6:
            attempts += 1
            child_ast = self._mutate_ast(node.ast, rng)
            if child_ast is None:
                continue
            if not audit(child_ast).ok:
                continue
            key = str(child_ast)
            if key in self._seen_ast:
                continue
            self._seen_ast.add(key)
            children_ast.append(child_ast)
        if not children_ast:
            return
        fits = self._eval_batch(children_ast)
        for child_ast, (ic, fitness) in zip(children_ast, fits):
            child = MctsNode(ast=child_ast, parent=node, depth=node.depth + 1)
            child.ic = float(ic)
            child.fitness = float(fitness)
            node.children.append(child)

    def _rollout(self, node: MctsNode, root_ic_abs: float, rng: np.random.Generator) -> float:
        """模拟：从节点随机走 rollout_steps 步变换，评估终态 IC 相对根基线的改进。"""
        cur = node.ast
        for _ in range(self.config.rollout_steps):
            nxt = self._mutate_ast(cur, rng)
            if nxt is None or not audit(nxt).ok:
                break
            cur = nxt
        ic, _ = self._eval_ast(cur)
        return max(0.0, abs(ic) - root_ic_abs)

    def _backprop(self, node: MctsNode, reward: float) -> None:
        """回传：自底向上累加 value_sum/visits。"""
        cur = node
        while cur is not None:
            cur.visits += 1
            cur.value_sum += reward
            cur = cur.parent

    # ─────────────────────────── 评估 ───────────────────────────

    def _eval_ast(self, ast: dict) -> Tuple[float, float]:
        """单点评估（主进程内联，返回 (ic, fitness)）。"""
        return _mcts_fitness_core(ast, self._fitness_state())

    def _eval_batch(self, asts: List[dict]) -> List[Tuple[float, float]]:
        """批量并行评估（joblib loky，与 GPMiner 相同模式）。"""
        workers = self.config.max_workers or min(8, os.cpu_count() or 8)
        if len(asts) <= 1 or workers <= 1:
            return [self._eval_ast(a) for a in asts]
        par = getattr(self, "_par", None)
        if par is None or par.n_jobs != workers:
            if par is not None:
                try:
                    par._terminate_backend()
                except Exception:
                    pass
            par = Parallel(n_jobs=workers, backend="loky", prefer="processes")
            self._par = par
        state = self._fitness_state()
        return par(delayed(_mcts_fitness_core)(a, state) for a in asts)

    def _fitness_state(self) -> dict:
        """构造 worker 可序列化求值上下文（含闭包 factor_value_fn 与短板根参照）。"""
        return {
            "factor_value_fn": self.factor_value_fn,
            "target": self.target,
            "min_samples": self.config.min_samples,
            "lambda_complexity": self.config.lambda_complexity,
            "lambda_corr": self.config.lambda_corr,
            "root_asts": getattr(self, "_root_asts", []),
        }

    # ─────────────────────────── AST 变换原语 ───────────────────────────

    def _mutate_ast(self, ast: dict, rng: np.random.Generator) -> Optional[dict]:
        """局部变换：参数变异 / 子树替换 / 算子替换 / 根包装（深度约束）。"""
        roll = rng.random()
        paths = _window_param_paths(ast)
        if paths and roll < 0.40:
            # 参数变异（FSA 方向偏好：高敏感参数优先——此处均匀随机，细化留待 FSA 后处理）
            path, base = paths[rng.integers(0, len(paths))]
            candidates = [w for w in self.config.windows if abs(w - base) > 1e-9]
            if not candidates:
                return self._mutate_ast(ast, rng) if roll < 0.5 else None
            new_val = float(int(rng.choice(candidates)))
            child = copy.deepcopy(ast)
            _set_at(child, path, {"c": new_val})
            return child
        if roll < 0.65:
            # 子树替换
            return self._subtree_replace(ast, rng)
        if roll < 0.85:
            # 算子替换
            return self._op_replace(ast, rng)
        # 根包装
        return self._wrap_root(ast, rng)

    def _random_ast(self, rng: np.random.Generator, depth: int) -> Optional[dict]:
        """随机 AST（深度约束；窗口类算子末参强制常量）。"""
        if depth >= self.config.max_depth:
            return self._random_leaf(rng)
        if depth > 0 and depth >= max(2, self.config.max_depth - 3) and rng.random() < 0.4:
            return self._random_leaf(rng)
        op = str(rng.choice(list(OP_REGISTRY.keys())))
        arity, _ = OP_REGISTRY.get(op, (0, None))
        if arity <= 0:
            return None
        args = []
        for i in range(arity):
            is_last = i == arity - 1
            if is_last and op in _WINDOW_OPS:
                args.append({"c": float(int(rng.choice(self.config.windows)))})
            else:
                child = self._random_ast(rng, depth + 1)
                if child is None:
                    return None
                args.append(child)
        return {"op": op, "args": args}

    def _random_leaf(self, rng: np.random.Generator) -> dict:
        if self.fields and rng.random() < 0.7:
            return {"f": str(rng.choice(self.fields))}
        return {"c": float(int(rng.choice(_CONST_VALUES)))}

    def _subtree_replace(self, ast: dict, rng: np.random.Generator) -> Optional[dict]:
        """随机节点（不含根）替换为随机新子树（深度约束）。"""
        nodes = [(p, n) for p, n in _collect_nodes(ast) if p]
        if not nodes:
            return None
        path, _ = nodes[rng.integers(0, len(nodes))]
        new_sub = self._random_ast(rng, depth=len(path))
        if new_sub is None:
            return None
        depth_after = _depth_at(ast, path) - 1 + _depth(new_sub)
        if depth_after > self.config.max_depth + 1:
            return None
        child = copy.deepcopy(ast)
        _set_at(child, path, new_sub)
        return child

    def _op_replace(self, ast: dict, rng: np.random.Generator) -> Optional[dict]:
        """内部节点算子替换为同 arity 算子（保持子结构，按 path 定位）。"""
        nodes = [
            (p, n) for p, n in _collect_nodes(ast)
            if "op" in n and n.get("args") and p
        ]
        if not nodes:
            return None
        path, node = nodes[rng.integers(0, len(nodes))]
        arity = len(node["args"])
        pool = (
            _UNARY_OPS if arity == 1
            else _BINARY_OPS if arity == 2
            else _TERNARY_OPS
        )
        candidates = [op for op in pool if op != node["op"]]
        if not candidates:
            return None
        child = copy.deepcopy(ast)
        # 替换后窗口算子语义变化：新算子可能不是窗口算子，原末位常量仍合法（常量叶子）
        _set_at(child, path, {"op": str(rng.choice(candidates)), "args": copy.deepcopy(node["args"])})
        return child

    def _wrap_root(self, ast: dict, rng: np.random.Generator) -> Optional[dict]:
        """根包装：外层套一层单目/滚动算子（rank / abs / ts_rank / mean 等）。"""
        op = str(rng.choice(_UNARY_OPS + ["ts_rank", "mean", "delta", "std"]))
        arity, _ = OP_REGISTRY.get(op, (0, None))
        if arity == 1:
            return {"op": op, "args": [copy.deepcopy(ast)]}
        # 滚动算子：末位窗口常量
        w = float(int(rng.choice(self.config.windows)))
        return {"op": op, "args": [copy.deepcopy(ast), {"c": w}]}


def _depth_at(ast: dict, path: Tuple[int, ...]) -> int:
    """根到 path 处节点的深度。"""
    node = ast
    d = 1
    for i in path:
        node = node["args"][i]
        d += 1
    return d
