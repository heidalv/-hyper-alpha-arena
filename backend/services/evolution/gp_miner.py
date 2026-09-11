"""
GP 因子挖掘器（v6 计划 5.3.1，对标幻方/WorldQuant 挖掘方法论）。

核心设计（纯 Python + numpy，不引入 gplearn）：
- 种群 300 个体：AST 由项目 DSL（expr/ops.py 算子注册表）生成，深度 2-5
- 适应度：fitness = |IC_rank| − λ1×复杂度(节点数) − λ2×与精英池最大相关
  （|IC| 双向 alpha 均纳入进化压力；复杂度与相关性惩罚防公式膨胀与同质化）
- 进化算子：锦标赛选择(3 取 1) + 子树交叉(70%) + 点/子树变异(20%) + 精英保留(top 5%)
- 早停：连续 patience 代无适应度提升或达 generations 上限
- 多种子：3-6 个种子并行跑（幻方 6 种子方法论），结果合并
- 输出：按适应度排序的 top 因子经 AlphaPool.try_admit 池感知准入（复用现有逻辑）

接入点：factor_evolution_loop._mine_candidates 替换 AlphaMiner 纯随机搜索段。
"""
from __future__ import annotations

import copy
import logging
import os
import time

# [2026-08-05 v6 10.2.2] loky worker 内限制 BLAS/OpenMP 线程数为 1：
# 32 个 worker 进程 × 默认多线程 BLAS = O(N^2) 原生线程竞争，
# 实测 32 进程并行反而负加速（0.89x）。必须在本模块 import numpy 之前设置，
# 这样 loky spawn 的子进程执行本模块时 numpy 以单线程 BLAS 初始化。
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import numpy as np
from joblib import Parallel, delayed

from backend.services.factor_engine.expr.audit import audit
from backend.services.factor_engine.expr.ops import LOOKAHEAD_BANNED_OPS, OP_REGISTRY
from backend.services.factor_engine.expr.parser import FactorExpr, parse
from backend.services.evolution.alpha_miner import AlphaPool
# [P3.2 2026-09-03] 三条挖矿路径共用的目标实现：分段 ICIR + 含成本净收益
from backend.services.evolution import fitness_objective as _fo

logger = logging.getLogger(__name__)

# 窗口/标量参数类算子：最后一个参数强制为常量（避免把数组当窗口长度）
_WINDOW_OPS = frozenset({
    "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank", "delta",
    "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin", "scale",
    "corr", "cov", "ts_corr",
})
_WINDOW_VALUES = [3, 5, 10, 20, 50]
_SCALE_VALUES = [1, 2, 5, 10]
_CONST_VALUES = [1, 2, 3, 5, 10, 20]
_NEG_INF = float("-inf")


def _count_nodes(ast: dict) -> int:
    """AST 节点数（模块级，loky worker 可调用）。"""
    if "args" not in ast:
        return 1
    return 1 + sum(_count_nodes(a) for a in ast["args"])


def _turnover_flip_rate(fv: np.ndarray) -> float:
    """[item11 2026-08-21] 信号方向平均翻转率（0=恒向，2=逐bar反转），换手成本代理。

    NaN 相邻差剔除后取均值；CPU/GPU 两条适应度路径共用（gp_gpu_eval 同款 import）。
    """
    try:
        sgn = np.sign(np.asarray(fv, dtype=float))
        d = np.diff(sgn)
        d = d[np.isfinite(d)]
        return float(np.mean(np.abs(d))) if d.size else 0.0
    except Exception:
        return 0.0


def _fitness_core(ast: dict, state: dict) -> float:
    """模块级适应度核心：fitness = |IC_rank| − λ1×复杂度 − λ2×与精英池最大相关。

    state（由 GPMiner._fitness_state 构造，loky cloudpickle 可序列化，
    含闭包 factor_value_fn）在 worker 进程反序列化后可直接求值。
    """
    factor_value_fn = state["factor_value_fn"]
    target = state["target"]
    try:
        fv = factor_value_fn({"expr": parse(ast)})
    except Exception:
        return _NEG_INF
    fv = np.asarray(fv, dtype=float)
    if fv.ndim == 0:
        fv = np.full_like(target, float(fv))
    if fv.shape != target.shape:
        return _NEG_INF
    mask = np.isfinite(fv) & np.isfinite(target)
    if mask.sum() < state["min_samples"]:
        return _NEG_INF
    if np.std(fv[mask]) < 1e-12:
        return _NEG_INF  # 常数因子
    ic = abs(float(np.corrcoef(fv[mask], target[mask])[0, 1]))
    if not np.isfinite(ic):
        return _NEG_INF
    # [FIX-2 2026-08-19] objective 含 icir 时与 GPU 路径同口径：
    # ICIR = |mean|/std 仅在 >=2 个有效币段且 std 非退化时定义，否则回退 |IC|。
    # [P3.2 2026-09-03] 实现下沉到 fitness_objective.segment_icir（GP/MCTS 共用）；
    # lens 现由 GPMiner 直接携带（此前无 GPU 上下文时拿不到 lens，icir 静默退化成 |IC|）。
    obj = ic
    _objective = (state.get("objective") or "ic").strip().lower()
    if _fo.objective_uses_icir(_objective):
        obj = _fo.segment_icir(fv, target, mask, state.get("lens"), fallback=ic)
    # [P3.2 2026-09-03] 含成本净收益并入目标（icir_net 默认；ic/icir 原样返回，可回滚）。
    # 用真实前瞻收益 + FACTOR_SCORER_COST（与晋升门禁同源），挖矿不再优化"扣费后亏钱的高 IC"。
    obj = _fo.blend_objective(obj, ic, fv, state.get("net_ctx"),
                              min_samples=int(state["min_samples"]))
    if not np.isfinite(obj):
        return _NEG_INF
    # 复杂度惩罚（节点数）
    penalty_c = state["lambda_complexity"] * _count_nodes(ast)
    # 与精英池最大相关惩罚（防同质化）
    corr_pen = 0.0
    elite_fvs = state.get("elite_fvs") or []
    if elite_fvs:
        max_corr = 0.0
        for e_fv in elite_fvs:
            e_fv = np.asarray(e_fv, dtype=float)
            if e_fv.shape != fv.shape:
                continue
            m2 = np.isfinite(fv) & np.isfinite(e_fv)
            if m2.sum() < 10:
                continue
            c = abs(float(np.corrcoef(fv[m2], e_fv[m2])[0, 1]))
            if np.isfinite(c):
                max_corr = max(max_corr, c)
        corr_pen = state["lambda_corr"] * max_corr
    elif state.get("elite_ast"):
        # 兼容旧路径（无预计算缓存时）
        max_corr = 0.0
        for e_ast in state["elite_ast"]:
            try:
                e_fv = np.asarray(factor_value_fn({"expr": parse(e_ast)}), dtype=float)
            except Exception:
                continue
            if e_fv.ndim == 0:
                e_fv = np.full_like(fv, float(e_fv))
            if e_fv.shape != fv.shape:
                continue
            m2 = np.isfinite(fv) & np.isfinite(e_fv)
            if m2.sum() < 10:
                continue
            c = abs(float(np.corrcoef(fv[m2], e_fv[m2])[0, 1]))
            if np.isfinite(c):
                max_corr = max(max_corr, c)
            corr_pen = state["lambda_corr"] * max_corr
    # [item11 2026-08-21] 换手成本惩罚：与 GPU 路径同口径
    _lam_to = float(state.get("lambda_turnover") or 0.0)
    turnover_pen = _lam_to * _turnover_flip_rate(fv) if _lam_to > 0 else 0.0
    # [2026-09-07 AlphaGen 组合增量] 奖励「加入后池组合 IC 的提升」，而非只奖单因子。
    # 此前适应度只优化单因子 ICIR，池筛（增量相关≤0.5）是拒绝式事后把关 →
    # 池里容易堆同族高 ICIR 因子（实测活跃池全是 rev5/10/20/50）。这里把
    # 「候选 + 精英池等权组合」的 |IC| 与「精英池单独」的 |IC| 之差作为奖励项，
    # 因子为组合而生。lambda_combo=0 回滚旧行为。
    combo_bonus = 0.0
    _lam_combo = float(state.get("lambda_combo") or 0.0)
    if _lam_combo > 0 and elite_fvs:
        try:
            def _z(x):
                x = np.asarray(x, dtype=float)
                m = np.isfinite(x)
                if m.sum() < 10 or np.std(x[m]) < 1e-12:
                    return None
                out = np.full_like(x, np.nan)
                out[m] = (x[m] - np.mean(x[m])) / (np.std(x[m]) + 1e-12)
                return out
            pool_parts = [pz for pz in (_z(e) for e in elite_fvs) if pz is not None]
            if pool_parts:
                pool_sig = np.nanmean(np.column_stack(pool_parts), axis=1)
                cand_z = _z(fv)
                if cand_z is not None:
                    combo_sig = np.nanmean(np.column_stack(pool_parts + [cand_z]), axis=1)
                    mm = np.isfinite(pool_sig) & np.isfinite(combo_sig) & np.isfinite(target)
                    if mm.sum() >= state["min_samples"]:
                        pool_ic = abs(float(np.corrcoef(pool_sig[mm], target[mm])[0, 1]))
                        combo_ic = abs(float(np.corrcoef(combo_sig[mm], target[mm])[0, 1]))
                        if np.isfinite(pool_ic) and np.isfinite(combo_ic):
                            combo_bonus = _lam_combo * (combo_ic - pool_ic)
        except Exception:
            combo_bonus = 0.0
    return float(obj - penalty_c - corr_pen - turnover_pen + combo_bonus)


@dataclass
class GPConfig:
    """GP 挖掘配置（5.3.1）。"""
    population_size: int = 300
    generations: int = 20
    elite_ratio: float = 0.05
    tournament_size: int = 3
    crossover_rate: float = 0.7
    mutation_rate: float = 0.2
    max_depth: int = 5
    min_depth: int = 2
    lambda_complexity: float = 1e-3      # 复杂度惩罚系数（5.3.1，可调）
    lambda_corr: float = 0.05            # 与精英池最大相关惩罚系数
    patience: int = 3                    # 早停：连续 3 代无提升
    n_seeds: int = 6                     # 幻方 6 种子方法论（每种子独立 Parallel）
    top_k_admit: int = 50                # 进化结束取 top 个候选做池准入
    min_samples: int = 50                # 有效样本下限
    seed_values: Optional[List[int]] = None   # 显式种子列表（测试用）
    max_workers: int = 0                 # 0 = min(32, cpu) 线程并行（v6 10.2.2：32 线程并行评估是本地算力主力）
    # [R0 升级] ε-lexicase 选择（GPU 路径自动生效；loky 回退锦标赛）
    selection: str = "lexicase"          # tournament | lexicase
    lexicase_eps: float = 1e-4           # 案例 IC 容差
    # [R1 升级] 目标与协同奖励
    # [2026-09-02 口径校准] objective 默认 ic → icir：准入闸门 fitness 判定用
    # ICIR（factor_card/admission_gate 2026-08-27 校准后），挖掘若按原始 IC 选优
    # 会与晋升口径脱节——高分 IC 因子在闸门处因 ICIR 不足被拒（"挖掘分数过低"的
    # 机制之一）。icir 是含波动惩罚的信号质量口径，与闸门同源。
    # [P3.2 2026-09-03] icir → icir_net：再并入"含成本净收益"项（fitness_objective）。
    # 晋升门禁 FactorBacktestScorer 按扣费后 walk-forward 净收益打分，挖掘只看
    # IC/ICIR 会把"IC 高但逐步翻转、扣费后亏钱"的因子推到门禁前白白淘汰。
    # 取值：ic | icir（旧行为）| icir_net（默认）| ic_net | net（IC 仅初筛）。
    # GP 与 MCTS 共用 FACTOR_GP_OBJECTIVE 一个开关（factor_evolution_loop 注入）。
    objective: str = _fo.DEFAULT_OBJECTIVE
    lambda_hof: float = 0.1              # 名人堂协同惩罚系数（低冗余因子集）
    # [item11 2026-08-21] 换手成本惩罚系数：适应度 − λ_to×方向翻转率。
    # 原实现只在最终闸门扣成本，挖掘期高换手（逐bar翻转）因子与打分口径脱节。
    # 翻转率∈[0,2]（0=恒向，2=逐bar反转）；λ=0.01 时逐bar反转者被扣 0.02
    # （≈半个 |IC| 量级），普通低频因子（<0.1 翻转）几乎无感。
    lambda_turnover: float = 0.01
    # [2026-09-07 AlphaGen 组合增量奖励] 适应度 + λ_combo×(候选入池后组合|IC| − 池|IC|)。
    # 奖励对组合有边际贡献的因子，而非只奖单因子高 ICIR——打破同族垄断。
    # 0 = 关闭（回滚旧行为）。默认 0.5：组合 IC 提升 0.02 即 +0.01 适应度。
    lambda_combo: float = 0.5
    # [R2 升级] ALPS 年龄分层（防早熟保创新）
    alps: bool = True
    alps_max_age: int = 12               # 超龄个体重播为随机新生（创新注入）


# ── [FIX-6 2026-08-19] 挖掘断点续训：已完成种子的候选原子落盘，重启后跳过续跑 ──
def _save_mine_progress(path: str, done_seeds, best_by_seed) -> None:
    import json as _json

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {
            "done_seeds": sorted(int(s) for s in done_seeds),
            "candidates": [{"ast": a, "fitness": float(f)} for a, f in best_by_seed],
        }
        _tmp = path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)
        os.replace(_tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.debug("[GPMiner] checkpoint 落盘失败: %s", e)


def _load_mine_progress(path: str):
    import json as _json

    try:
        if not os.path.exists(path):
            return set(), []
        with open(path, "r", encoding="utf-8") as f:
            payload = _json.load(f)
        done = set(int(s) for s in (payload.get("done_seeds") or []))
        cands = [
            (c["ast"], float(c["fitness"]))
            for c in (payload.get("candidates") or [])
            if isinstance(c, dict) and "ast" in c
        ]
        return done, cands
    except Exception as e:  # noqa: BLE001
        logger.debug("[GPMiner] checkpoint 读取失败: %s", e)
        return set(), []


class GPMiner:
    """
    GP 因子挖掘器。

    用法：
        miner = GPMiner(fields, factor_value_fn, target, pool, config)
        admitted = miner.mine()   # 返回 [(expr, contribution), ...]
    """

    def __init__(
        self,
        fields: List[str],
        factor_value_fn: Callable[[dict], np.ndarray],
        target: np.ndarray,
        pool: AlphaPool,
        config: Optional[GPConfig] = None,
        gpu_ctx=None,
        lens: Optional[List[int]] = None,
        fwd_ret: Optional[np.ndarray] = None,
        horizon: Optional[int] = None,
    ):
        self.fields = [f for f in fields if f]
        self.factor_value_fn = factor_value_fn
        self.target = np.asarray(target, dtype=float)
        self.pool = pool
        self.config = config or GPConfig()
        # [P3.2 2026-09-03] 面板币段长度 + 真实前瞻收益 + 前瞻根数：
        # lens 让 CPU 路径的 ICIR 不再依赖 GPU 上下文；fwd_ret/horizon 供含成本净收益项
        # （target 在 5m/15m 可能是三重障碍 ±1 标签，扣不了 bp，必须另带真实收益）。
        self.lens: Optional[List[int]] = [int(x) for x in lens] if lens else None
        self.fwd_ret: Optional[np.ndarray] = (
            np.asarray(fwd_ret, dtype=float) if fwd_ret is not None else None
        )
        if self.fwd_ret is not None and self.fwd_ret.shape != self.target.shape:
            logger.warning(
                "[GPMiner] fwd_ret 形状 %s 与 target %s 不一致，净收益项停用",
                self.fwd_ret.shape, self.target.shape,
            )
            self.fwd_ret = None
        self.horizon: int = max(1, int(horizon or 1))
        self._net_ctx_cache: Optional[dict] = None
        # [2026-08-14 P1-G1] 剔除单序列前视算子（rank/cs_rank/scale 已被 audit 禁）
        self._op_names = [n for n in OP_REGISTRY.keys() if n not in LOOKAHEAD_BANNED_OPS]
        # 各代精英（防同质化相关性惩罚的参照系）
        self._elite_ast: List[dict] = []
        # [2026-08-17 GPU] 精英因子值缓存（每代只算一次；GPU 上下文可选）
        self._gpu_ctx = gpu_ctx
        self._elite_fvs: List[np.ndarray] = []
        # [R0/R1 升级] 案例得分缓存（str(ast) → (S,) 案例 IC）与名人堂（ast, values, fitness）
        self._case_cache: dict = {}
        self._hof: List[Tuple[dict, np.ndarray, float]] = []
        self._last_vals = None
        self._last_population: List[dict] = []

    # ─────────────────────────── 对外入口 ───────────────────────────

    def close(self) -> None:
        """释放并行评估进程池（loky workers）。调用方在长任务结束后调用。"""
        par = getattr(self, "_par", None)
        if par is not None:
            try:
                par._terminate_backend()
            except Exception:
                pass
            self._par = None

    def mine(
        self,
        pool_factor_matrix: Optional[np.ndarray] = None,
        max_workers: Optional[int] = None,
        warm_start_seeds: Optional[List[dict]] = None,
        budget_deadline: Optional[float] = None,
        progress_path: Optional[str] = None,
    ) -> List[Tuple[FactorExpr, float]]:
        """
        多种子并行进化 → 合并 top 候选 → AlphaPool.try_admit 池感知准入。

        返回被接纳的 (expr, contribution) 列表（与 AlphaMiner.mine_random 同构，
        可无缝替换接入 _mine_candidates）。
        [R3] warm_start_seeds：LLM 生成的种子注入每个种子的初始种群（探索+开采双引擎）。
        [FIX-3 2026-08-19] budget_deadline：绝对截止时间（time.time()）。种子串行+代循环
        每步检查，耗尽即停（保留已完成种子候选），避免 6 种子串行跑 9~15h 阻塞后续周期。
        未显式传入时按 FACTOR_GP_BUDGET_SEC（默认 1800）自算。
        [FIX-6 2026-08-19] progress_path：断点续训文件；每种子完成后落盘，重启后跳过已完成种子。
        """
        if budget_deadline is None:
            _budget_sec = float(os.getenv("FACTOR_GP_BUDGET_SEC", "1800") or 1800)
            _budget_sec = max(60.0, min(_budget_sec, 7200.0))
            budget_deadline = time.time() + _budget_sec
        seeds = self.config.seed_values or list(range(self.config.n_seeds))
        workers = max_workers or self.config.max_workers or min(32, os.cpu_count() or 32)

        # 种子串行（共享 self 状态非线程安全）；种群评估每次新建 Parallel，
        # 根因修复：禁止跨种子复用同一 Parallel 实例导致 already running。
        # [FIX-6 2026-08-19] 断点续训：加载已完成种子候选、跳过已完成种子、每种子完成后落盘。
        done_seeds: set = set()
        best_by_seed: List[Tuple[dict, float]] = []
        if progress_path:
            done_seeds, best_by_seed = _load_mine_progress(progress_path)
            if done_seeds:
                logger.info(
                    "[GPMiner] 断点续训：跳过已完成种子 %s，恢复候选 %d",
                    sorted(done_seeds), len(best_by_seed),
                )
        for _s in seeds:
            if int(_s) in done_seeds:
                continue
            if time.time() > budget_deadline:
                logger.warning(
                    "[GPMiner] 挖掘预算耗尽，停止剩余种子（已累计 %d 个候选，种子 %d/%d）",
                    len(best_by_seed), seeds.index(_s), len(seeds),
                )
                break
            try:
                best_by_seed.extend(self._run_seed(_s, warm_start_seeds=warm_start_seeds, budget_deadline=budget_deadline))
                if progress_path:
                    done_seeds.add(int(_s))
                    _save_mine_progress(progress_path, done_seeds, best_by_seed)
            except Exception as e:
                logger.warning(f"[GPMiner] 种子挖掘异常: {e}")

        # 按适应度排序取 top_k_admit
        best_by_seed.sort(key=lambda x: x[1], reverse=True)
        top = best_by_seed[: self.config.top_k_admit]
        logger.info(
            f"[GPMiner] 多种子进化完成: {len(seeds)} 种子(独立Parallel评估), "
            f"候选 {len(best_by_seed)}, 准入尝试 {len(top)}"
        )

        self.close()

        # 池感知准入（复用 AlphaPool.try_admit 现有逻辑）
        # [M2 口径同一律] 准入目标与挖掘适应度同口径（GPU 中性化目标优先）
        _adm_target = self.target
        if self._gpu_ctx is not None and getattr(self._gpu_ctx, "_neutralized", False):
            _adm_target = self._gpu_ctx._target
        admitted: List[Tuple[FactorExpr, float]] = []
        for ast, _fit in top:
            try:
                expr = parse(ast)
                fv = self.factor_value_fn({"expr": expr})
                fv_arr = np.asarray(fv, dtype=float)
                if fv_arr.ndim == 0:
                    fv_arr = np.full_like(_adm_target, float(fv_arr))
                ok, contribution = self.pool.try_admit(
                    expr, fv_arr, _adm_target, pool_factor_matrix=pool_factor_matrix,
                )
            except Exception:
                continue
            if ok:
                admitted.append((expr, contribution))
        logger.info(f"[GPMiner] 池准入: {len(admitted)}/{len(top)} 命中")
        return admitted

    # ─────────────────────────── 单种子进化 ───────────────────────────

    def _run_seed(
        self,
        seed: int,
        warm_start_seeds: Optional[List[dict]] = None,
        budget_deadline: Optional[float] = None,
    ) -> List[Tuple[dict, float]]:
        """单种子完整进化过程，返回 (ast, fitness) 列表（全代精英合并）。"""
        rng = np.random.default_rng(seed)
        # 初始种群（audit 过滤）；[R2 ALPS] 并行年龄；[R3] LLM 热启动种子优先
        population: List[dict] = []
        ages: List[int] = []
        for _w in (warm_start_seeds or []):
            if len(population) >= self.config.population_size:
                break
            if _w is None:
                continue
            try:
                if audit(_w).ok:
                    population.append(copy.deepcopy(_w))
                    ages.append(1)
            except Exception:
                continue
        while len(population) < self.config.population_size:
            ast = self._random_ast(rng, depth=0)
            if ast is None:
                continue
            if audit(ast).ok:
                population.append(ast)
                ages.append(1)

        best_history: List[Tuple[dict, float]] = []
        global_best = _NEG_INF
        no_improve = 0
        workers = self.config.max_workers or min(32, os.cpu_count() or 32)

        for gen in range(self.config.generations):
            # [FIX-3 2026-08-19] 预算截止：单种子也可能跑多代（每代数分钟），代级检查
            if budget_deadline is not None and time.time() > budget_deadline:
                logger.warning("[GPMiner] 种子%d 第%d代前预算耗尽，提前终止", seed, gen)
                break
            # 适应度评估（并行）
            fits = self._eval_population(population, workers)
            scored = sorted(
                [(ast, f, ages[i]) for i, (ast, f) in enumerate(zip(population, fits)) if np.isfinite(f)],
                key=lambda x: x[1], reverse=True,
            )
            if not scored:
                logger.warning(f"[GPMiner] 种子{seed} 第{gen}代全部无效，提前终止")
                break

            gen_best = scored[0][1]
            best_history.append((scored[0][0], scored[0][1]))
            # 精英存档（防同质化参照系，取前 5%）
            n_elite = max(1, int(len(scored) * self.config.elite_ratio))
            self._elite_ast = [copy.deepcopy(a) for a, _, _ in scored[:n_elite]]

            # [R1] 名人堂更新：本代 top-k（值取自 GPU 求值矩阵，协同奖励参照系）
            if getattr(self, "_last_vals", None) is not None and getattr(self, "_last_population", None):
                _order = sorted(
                    range(len(fits)),
                    key=lambda i: fits[i] if np.isfinite(fits[i]) else _NEG_INF,
                    reverse=True,
                )
                self._hof = [
                    (copy.deepcopy(self._last_population[i]), self._last_vals[i].copy(), fits[i])
                    for i in _order[:10] if np.isfinite(fits[i])
                ]

            # 早停
            if gen_best > global_best + 1e-9:
                global_best = gen_best
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= self.config.patience:
                    logger.info(f"[GPMiner] 种子{seed} 第{gen}代早停 (best={gen_best:.4f})")
                    break

            # 精英保留 + 生成下一代（[R2] 年龄随代数增长，超龄重播新生）
            elites = [(copy.deepcopy(a), _age) for a, _, _age in scored[:n_elite]]
            n_offspring = self.config.population_size - len(elites)
            offspring: List[Tuple[dict, int]] = []
            while len(offspring) < n_offspring:
                p1, a1 = self._select_parent(scored, rng)
                p2, a2 = self._select_parent(scored, rng)
                child = copy.deepcopy(p1)
                if rng.random() < self.config.crossover_rate:
                    child = self._crossover(p1, p2, rng)
                if rng.random() < self.config.mutation_rate:
                    child = self._mutate(child, rng)
                if child is None or not audit(child).ok:
                    continue
                child_age = max(int(a1), int(a2)) + 1
                # [R2] 超龄重播：年龄上限以上的个体替换为随机新生（创新注入）
                if self.config.alps and child_age > int(self.config.alps_max_age):
                    _fresh = self._random_ast(rng, depth=0)
                    if _fresh is None or not audit(_fresh).ok:
                        continue
                    child = _fresh
                    child_age = 1
                offspring.append((child, child_age))
            population = [a for a, _ in elites] + [a for a, _ in offspring]
            ages = [_a for _, _a in elites] + [_a for _, _a in offspring]
            if not population:
                break

        # 返回全代历史最优（去重保留 best per ast 序列化）
        seen: set = set()
        merged: List[Tuple[dict, float]] = []
        for ast, f in sorted(best_history, key=lambda x: x[1], reverse=True):
            key = str(ast)
            if key not in seen:
                seen.add(key)
                merged.append((ast, float(f)))
        logger.info(f"[GPMiner] 种子{seed} 完成: {len(merged)} 个最优候选, best={merged[0][1]:.4f}" if merged else f"[GPMiner] 种子{seed} 无有效候选")
        return merged

    def _eval_population(
        self, population: List[dict], workers: Optional[int] = None
    ) -> List[float]:
        """评估种群适应度（v6 10.2.2：32 线程并行评估主力）。

        实现：joblib loky 进程级并行（计划指定 joblib 优先）。
        - 闭包 factor_value_fn 与 numpy 数据打包为轻量 state 字典，由 loky 的
          cloudpickle 序列化（标准 pickle 不可；实例级序列化含 AlphaPool 等不可）。
        - 实测：ThreadPoolExecutor 因 numpy GIL/BLAS 争用为负加速（0.84x），
          弃用线程池；loky 每进程独立解释器，E5-2698B 16C/32T 目标 >=4-6x。
        - 每次评估新建 Parallel 并在 finally 终止，禁止跨种子复用导致 already running。
        - [2026-08-17 GPU] FACTOR_EVO_GPU_EVAL=1 时走栈式 GPU 批量求值
          （等价性验收通过后），失败自动回退本路径。
        """
        workers = workers or self.config.max_workers or min(32, os.cpu_count() or 32)
        if len(population) <= 1:
            return [self._fitness(population[0])] if population else []
        # 精英值缓存刷新（每代一次）：原实现每棵树重算全部精英 → O(N×E) 浪费
        self._refresh_elite_cache()
        # ── GPU 路径（GPU 子集向量化 + CPU 子集 loky 并行） ──
        if self._gpu_ctx is not None and len(population) >= 8:
            try:
                vals, gpu_mask = self._gpu_ctx.eval_values(population)
                if vals is not None:
                    from backend.services.evolution.gp_gpu_eval import (
                        compute_fitness_from_values,
                    )
                    fits: List[float] = [float("-inf")] * len(population)
                    gpu_idx = [i for i, m in enumerate(gpu_mask) if m]
                    cpu_idx = [i for i, m in enumerate(gpu_mask) if not m]
                    if gpu_idx:
                        node_counts = [_count_nodes(population[i]) for i in gpu_idx]
                        # [M2 口径同一律] 适应度目标 = GPU 上下文的中性化目标（若启用）
                        _fit_target = getattr(self._gpu_ctx, "_target", self.target)
                        # [R0/R1] 案例 IC + ICIR 目标 + 名人堂协同奖励
                        fg, case_ics = compute_fitness_from_values(
                            vals[gpu_idx], [population[i] for i in gpu_idx],
                            _fit_target,
                            min_samples=self.config.min_samples,
                            lam_c=self.config.lambda_complexity,
                            lam_corr=self.config.lambda_corr,
                            elite_fvs=self._elite_fvs,
                            node_counts=node_counts,
                            lens=getattr(self._gpu_ctx, "lens", None),
                            objective=self.config.objective,
                            lam_hof=self.config.lambda_hof,
                            hof_values=[v for _, v, _ in self._hof],
                            lam_to=self.config.lambda_turnover,  # [item11] 换手惩罚同口径
                            net_ctx=self._net_ctx(),  # [P3.2] 含成本净收益同口径
                        )
                        for i, f in zip(gpu_idx, fg):
                            fits[i] = f
                        # 案例缓存（ε-lexicase 用）
                        for j, i in enumerate(gpu_idx):
                            self._case_cache[str(population[i])] = case_ics[j]
                    if cpu_idx:
                        par = Parallel(n_jobs=workers, backend="loky", prefer="processes")
                        state = self._fitness_state()
                        try:
                            fc = par(delayed(_fitness_core)(population[i], state) for i in cpu_idx)
                        finally:
                            try:
                                par._terminate_backend()
                            except Exception:
                                pass
                        for i, f in zip(cpu_idx, fc):
                            fits[i] = f
                    # [R0/R1] 保存值矩阵供名人堂/案例缓存消费
                    self._last_vals = vals
                    self._last_population = [copy.deepcopy(a) for a in population]
                    return fits
            except Exception as _gpu_err:
                logger.warning("[GPMiner GPU] 求值异常，回退 loky: %s", _gpu_err)
        if workers <= 1:
            return [self._fitness(a) for a in population]
        # 每次评估新建 Parallel，禁止跨种子/跨线程复用同一实例
        par = Parallel(n_jobs=workers, backend="loky", prefer="processes")
        state = self._fitness_state()
        try:
            return par(delayed(_fitness_core)(a, state) for a in population)
        finally:
            try:
                par._terminate_backend()
            except Exception:
                pass

    def _refresh_elite_cache(self) -> None:
        """每代刷新精英因子值缓存（GPU 可用走 GPU，否则 numpy 一次性算好）。"""
        if self._gpu_ctx is not None:
            try:
                self._gpu_ctx.refresh_elites(self._elite_ast)
                self._elite_fvs = list(self._gpu_ctx.elite_fvs)
                return
            except Exception as _ge:
                logger.debug("[GPMiner] GPU 精英缓存失败，回退 numpy: %s", _ge)
        fvs: List[np.ndarray] = []
        for e_ast in self._elite_ast:
            try:
                fv = np.asarray(self.factor_value_fn({"expr": parse(e_ast)}), dtype=float)
                if fv.ndim == 0:
                    fv = np.full_like(self.target, float(fv))
                if fv.shape == self.target.shape:
                    fvs.append(fv)
            except Exception:
                continue
        self._elite_fvs = fvs

    def _fitness_state(self) -> dict:
        """构造 worker 可序列化的适应度求值上下文（含闭包 factor_value_fn）。"""
        # [FIX-2 2026-08-19] 携带 objective + lens，使 CPU 兜底路径与 GPU 路径
        # 适应度口径一致（objective="icir" 时按币段算 ICIR）。
        return {
            "factor_value_fn": self.factor_value_fn,
            "target": self.target,
            "min_samples": self.config.min_samples,
            "lambda_complexity": self.config.lambda_complexity,
            "lambda_corr": self.config.lambda_corr,
            # [item11] 换手成本惩罚（CPU 兜底路径与 GPU 同口径）
            "lambda_turnover": self.config.lambda_turnover,
            # [2026-09-07] AlphaGen 组合增量奖励系数
            "lambda_combo": float(getattr(self.config, "lambda_combo", 0.0) or 0.0),
            "objective": self.config.objective,
            "lens": self._panel_lens(),
            # [P3.2 2026-09-03] 含成本净收益上下文（objective 不含 net 时仅含 objective 键）
            "net_ctx": self._net_ctx(),
            "elite_ast": self._elite_ast,
            # [2026-08-17 GPU] 精英值预计算缓存（每代一次，_fitness_core 优先用）
            "elite_fvs": list(self._elite_fvs),
        }

    def _panel_lens(self) -> Optional[List[int]]:
        """币段长度：显式 lens 优先，其次 GPU 上下文的 lens（历史行为）。"""
        if self.lens:
            return list(self.lens)
        if self._gpu_ctx is not None:
            _lens = list(getattr(self._gpu_ctx, "lens", None) or [])
            return _lens or None
        return None

    def _net_ctx(self) -> dict:
        """[P3.2] 净收益求值上下文（构造一次复用；随 state 序列化给 loky worker）。"""
        if self._net_ctx_cache is None:
            self._net_ctx_cache = _fo.net_context(
                objective=self.config.objective,
                fwd_ret=self.fwd_ret,
                horizon=self.horizon,
                lens=self._panel_lens(),
            )
        return self._net_ctx_cache

    # ─────────────────────────── 适应度 ───────────────────────────

    def _fitness(self, ast: dict) -> float:
        """适应度入口（主进程直接调用；worker 走模块级 _fitness_core）。"""
        return _fitness_core(ast, self._fitness_state())

    # ─────────────────────────── AST 操作 ───────────────────────────

    def _random_ast(self, rng: np.random.Generator, depth: int) -> Optional[dict]:
        """随机生成 AST（深度 2-5；根节点强制为 op；窗口类算子末参强制常量）。"""
        if depth >= self.config.max_depth:
            return self._random_leaf(rng)
        if depth > 0 and depth >= self.config.min_depth and rng.random() < 0.4:
            return self._random_leaf(rng)
        op = str(rng.choice(self._op_names))
        arity, _ = OP_REGISTRY.get(op, (0, None))
        if arity <= 0:
            return None
        args = []
        for i in range(arity):
            is_last = i == arity - 1
            if is_last and op in _WINDOW_OPS:
                # 窗口/标量参数：强制常量
                consts = _SCALE_VALUES if op == "scale" else _WINDOW_VALUES
                args.append({"c": float(int(rng.choice(consts)))})
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

    def _node_count(self, ast: dict) -> int:
        if "args" not in ast:
            return 1
        return 1 + sum(self._node_count(a) for a in ast["args"])

    def _depth(self, ast: dict) -> int:
        if "args" not in ast or not ast["args"]:
            return 1
        return 1 + max(self._depth(a) for a in ast["args"])

    def _collect_nodes(self, ast: dict, path: Tuple[int, ...] = ()) -> List[Tuple[Tuple[int, ...], dict]]:
        nodes = [(path, ast)]
        if "args" in ast:
            for i, child in enumerate(ast["args"]):
                nodes.extend(self._collect_nodes(child, path + (i,)))
        return nodes

    def _set_at(self, ast: dict, path: Tuple[int, ...], new_node: dict) -> None:
        node = ast
        for i in path[:-1]:
            node = node["args"][i]
        node["args"][path[-1]] = new_node

    def _tournament_index(self, scored: List[Tuple[dict, float]], rng: np.random.Generator) -> int:
        k = min(self.config.tournament_size, len(scored))
        contenders = rng.choice(len(scored), size=k, replace=False)
        return int(max(contenders, key=lambda i: scored[i][1]))

    def _tournament_select(self, scored: List[Tuple[dict, float]], rng: np.random.Generator) -> dict:
        return scored[self._tournament_index(scored, rng)][0]

    # [R0 升级] ε-lexicase 选择：按案例序列 + ε 容差筛选，抗噪声、保多样性
    def _select_any(self, scored: List[Tuple[dict, float]], rng: np.random.Generator) -> dict:
        if self.config.selection == "lexicase" and self._case_cache:
            return self._lexicase_select(scored, rng)
        return self._tournament_select(scored, rng)

    # [R2 升级] ALPS 年龄分层选择：同层竞争，年轻层保护，超龄重播
    @staticmethod
    def _layer_of(age: int) -> int:
        if age <= 1:
            return 0
        if age <= 2:
            return 1
        if age <= 4:
            return 2
        if age <= 9:
            return 3
        return 4

    def _select_parent(self, scored: List[Tuple[dict, float, int]], rng: np.random.Generator):
        """层内选择 → (ast, age)。scored = [(ast, fitness, age), ...]。"""
        _two = [(a, f) for a, f, _ in scored]
        if not self.config.alps or len(scored) < 8:
            idx = self._tournament_index(_two, rng)
            return scored[idx][0], scored[idx][2]
        layers: dict = {}
        for i, (_, _, age) in enumerate(scored):
            layers.setdefault(self._layer_of(int(age)), []).append(i)
        if not layers:
            return scored[0][0], scored[0][2]
        keys = sorted(layers.keys())
        lyr = keys[int(rng.integers(0, len(keys)))]
        idxs = layers[lyr]
        if self.config.selection == "lexicase" and self._case_cache:
            sub = [scored[i] for i in idxs]
            ast = self._lexicase_select([(a, f) for a, f, _ in sub], rng)
            for i in idxs:
                if str(scored[i][0]) == str(ast):
                    return scored[i][0], scored[i][2]
            return ast, scored[idxs[0]][2]
        idx = int(idxs[rng.integers(0, len(idxs))])
        return scored[idx][0], scored[idx][2]

    def _lexicase_select(self, scored: List[Tuple[dict, float]], rng: np.random.Generator) -> dict:
        """ε-lexicase：洗牌案例顺序，逐案例保留「与最优差距 ≤ ε」的个体。"""
        eps = float(self.config.lexicase_eps)
        pool = list(range(len(scored)))
        cases = list(range(len(next(iter(self._case_cache.values()))))) if self._case_cache else []
        if not cases:
            return self._tournament_select(scored, rng)
        order = rng.permutation(cases)
        for c in order:
            if len(pool) == 1:
                break
            # 该案例上的得分（缺失 → 该案例不参与筛选，视作 -inf）
            vals = []
            for i in pool:
                cv = self._case_cache.get(str(scored[i][0]))
                v = float(cv[c]) if cv is not None and np.isfinite(cv[c]) else float("-inf")
                vals.append(v)
            best_v = max(vals)
            pool = [i for i, v in zip(pool, vals) if v >= best_v - eps]
            if not pool:
                pool = [max(range(len(scored)), key=lambda i: scored[i][1])]
                break
        if not pool:
            return scored[0][0]
        return scored[int(pool[rng.integers(0, len(pool))])][0]

    def _crossover(self, p1: dict, p2: dict, rng: np.random.Generator) -> dict:
        """子树交叉：p1 的随机节点替换为 p2 的随机子树（深度约束，超限保留原样）。"""
        child = copy.deepcopy(p1)
        nodes1 = self._collect_nodes(child)
        nodes2 = self._collect_nodes(p2)
        if len(nodes1) < 2 or not nodes2:
            return child
        path1, _ = nodes1[rng.integers(1, len(nodes1))]  # 不选根，避免整树替换
        _, sub2 = nodes2[rng.integers(0, len(nodes2))]
        # 深度约束：替换后整体深度不得超过 max_depth + 1
        depth_after = self._depth_at(child, path1) - 1 + self._depth(sub2)
        if depth_after <= self.config.max_depth + 1:
            self._set_at(child, path1, copy.deepcopy(sub2))
        return child

    def _depth_at(self, ast: dict, path: Tuple[int, ...]) -> int:
        """根到 path 处节点的深度。"""
        node = ast
        d = 1
        for i in path:
            node = node["args"][i]
            d += 1
        return d

    def _mutate(self, ind: dict, rng: np.random.Generator) -> Optional[dict]:
        """点/子树变异：随机节点替换为随机新子树。"""
        nodes = self._collect_nodes(ind)
        if len(nodes) < 2:
            return ind  # 只有根节点（path=()），无法安全变异
        # [2026-08-06 2.3 修复] 必须不选根节点：根 path=() 会使 _set_at 的
        # path[-1] 越界（IndexError: tuple index out of range）→ 种子异常 →
        # 挖掘 0 候选。与 _crossover 的"不选根"处理保持一致。
        path, node = nodes[int(rng.integers(1, len(nodes)))]
        new_sub = self._random_ast(rng, depth=len(path))
        if new_sub is None:
            return ind
        depth_after = self._depth_at(ind, path) - 1 + self._depth(new_sub)
        if depth_after > self.config.max_depth + 1:
            return ind
        child = copy.deepcopy(ind)
        self._set_at(child, path, new_sub)
        return child
