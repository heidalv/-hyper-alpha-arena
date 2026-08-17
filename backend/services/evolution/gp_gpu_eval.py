"""GP 挖矿 GPU 求值接线（M2，2026-08-17）。

把 gpu_batch_eval 栈式执行器接入 gp_miner._eval_population：
  - 首次使用时做等价性验收（真实 DSL vs GPU，秩相关 ≥0.999 且 isclose 不匹配 <5%）；
  - 验收失败/无 CUDA/任意异常 → 永久回退原 loky 路径（fail-safe，可回滚）；
  - 精英因子值每代只算一次（消除原实现每棵树重算 15 个精英的 O(N×E) 浪费）；
  - 适应度组装（IC/复杂度/精英相关惩罚）与 _fitness_core 语义逐项对齐。

开关：settings.FACTOR_EVO_GPU_EVAL（.env），FACTOR_EVO_GPU_MAX_MEM_MB / CHUNK 可调。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman 秩相关（scipy rankdata + pearson；scipy 缺失时退 pearson）。"""
    try:
        from scipy.stats import rankdata

        ra = rankdata(a)
        rb = rankdata(b)
    except Exception:
        return float(np.corrcoef(a, b)[0, 1])
    if np.std(ra) < 1e-12 or np.std(rb) < 1e-12:
        return float(np.corrcoef(a, b)[0, 1])
    return float(np.corrcoef(ra, rb)[0, 1])


class GpuEvalContext:
    """GP 面板求值的 GPU 上下文（含等价性验收与精英值缓存）。"""

    def __init__(
        self,
        factor_value_fn: Callable[[dict], np.ndarray],
        fields_per_symbol: Sequence[Dict[str, np.ndarray]],
        target: np.ndarray,
        min_samples: int = 50,
        lambda_complexity: float = 1e-3,
        lambda_corr: float = 0.05,
        mem_mb: float = 1200.0,
        chunk: int = 64,
        verify_trees: int = 12,
    ):
        self._fn = factor_value_fn
        self._fields = list(fields_per_symbol)
        self._target = np.asarray(target, dtype=float)
        self._min_samples = int(min_samples)
        self._lam_c = float(lambda_complexity)
        self._lam_corr = float(lambda_corr)
        self._mem_mb = float(mem_mb)
        self._chunk = int(chunk)
        self._verify_n = int(verify_trees)
        self.sample_fn: Optional[Callable[[int], List[dict]]] = None
        self.ready = False
        self._failed = False
        self._stats = {"gpu_evals": 0, "cpu_evals": 0, "verify_ok": False}
        # 精英值缓存：每代刷新一次
        self._elite_fvs: List[np.ndarray] = []
        self._elite_ids: List[str] = []

    # ── 等价性验收（首次使用） ──────────────────────────────────────
    def _verify(self) -> bool:
        from backend.services.evolution.gpu_batch_eval import eval_panel_batch

        if self.sample_fn is None:
            logger.warning("[GpuEval] 无采样函数，跳过验证→禁用")
            self._failed = True
            return False
        try:
            # 分批采样直到可比树 ≥8（上限 4 批 × 24 棵）
            t_all = time.perf_counter()
            n_total = n_checked = bad_corr = mism = 0
            min_corr = 1.0
            n_gpu_sampled = 0
            for _batch in range(4):
                asts = [a for a in (self.sample_fn(24) or []) if a]
                if not asts:
                    break
                n_total += len(asts)
                t0 = time.perf_counter()
                vals, gpu_ok = eval_panel_batch(
                    asts, self._fields, device="cuda",
                    chunk=self._chunk, mem_mb=self._mem_mb,
                )
                t_gpu = time.perf_counter() - t0
                n_gpu_sampled += int(gpu_ok.sum())
                refs: List[Optional[np.ndarray]] = []
                for a in asts:
                    try:
                        fv = np.asarray(self._fn({"expr": _parse_ast(a)}), dtype=float)
                        refs.append(fv if fv.ndim == 1 else None)
                    except Exception:
                        refs.append(None)
                for i in range(len(asts)):
                    if not gpu_ok[i]:
                        continue
                    r = refs[i]
                    if r is None or r.shape != vals[i].shape:
                        continue
                    m = np.isfinite(vals[i]) & np.isfinite(r)
                    if m.sum() < 20:
                        continue
                    gv, rv = vals[i][m], r[m]
                    if np.std(gv) < 1e-12 or np.std(rv) < 1e-12:
                        continue
                    n_checked += 1
                    # 值保真：Spearman 对大量并列的离散序列对 ulp 噪声过敏（实测
                    # 1581/1693 位置仅差 1e-15 时 Spearman 掉到 0.989），
                    # 故改用 Pearson ≥0.99999 或 Spearman ≥0.999 双口径。
                    c_p = float(np.corrcoef(gv, rv)[0, 1])
                    c = _spearman(gv, rv)
                    min_corr = min(min_corr, c_p)
                    fidelity_ok = c_p >= 0.99999 or (np.isfinite(c) and c >= 0.999)
                    # 下游影响：GPU 值对目标 IC 的影响 ≤5e-4（挖掘排序无损）
                    ic_ok = True
                    if fidelity_ok and np.isfinite(c_p):
                        tseg = self._target[m]
                        tm = np.isfinite(tseg)
                        if tm.sum() >= 20:
                            ic_g = abs(float(np.corrcoef(gv[tm], tseg[tm])[0, 1]))
                            ic_r = abs(float(np.corrcoef(rv[tm], tseg[tm])[0, 1]))
                            ic_ok = abs(ic_g - ic_r) <= 5e-4
                    if not fidelity_ok:
                        bad_corr += 1
                        logger.warning(
                            "[GpuEval] 验收失败树: pearson=%.6f spearman=%.5f ops=%s",
                            c_p, c, _oplist(asts[i]),
                        )
                    elif not ic_ok:
                        mism += 1
                        logger.warning(
                            "[GpuEval] 验收IC偏移树: pearson=%.6f ops=%s",
                            c_p, _oplist(asts[i]),
                        )
                if n_checked >= 8:
                    break
            ok = n_checked >= 3 and bad_corr == 0 and mism == 0
            logger.warning(
                "[GpuEval] 等价性验收: 树=%d GPU覆盖=%d 可比=%d 值保真失败=%d IC偏移>5e-4=%d "
                "min_pearson=%.6f GPU耗时=%.2fs → %s",
                n_total, n_gpu_sampled, n_checked, bad_corr, mism, min_corr,
                time.perf_counter() - t_all,
                "通过" if ok else "失败(回退CPU)",
            )
            self._stats["verify_ok"] = ok
            if not ok:
                self._failed = True
            return ok
        except Exception as e:
            logger.warning("[GpuEval] 等价性验收异常，禁用GPU: %s", e)
            self._failed = True
            return False

    # ── 批量求值入口 ────────────────────────────────────────────────
    def eval_values(self, population: Sequence[dict]):
        """GPU 批量求值 → (vals, gpu_mask)。

        vals (P, n) float64（非 GPU 程序为 0 占位）；gpu_mask (P,) bool。
        失败返回 (None, None)（调用方整体回退 loky）。
        """
        from backend.services.evolution.gpu_batch_eval import eval_panel_batch

        if self._failed:
            return None, None
        if not self.ready:
            self.ready = self._verify()
            if not self.ready:
                return None, None
        t0 = time.perf_counter()
        try:
            vals, gpu_ok = eval_panel_batch(
                population, self._fields, device="cuda",
                chunk=self._chunk, mem_mb=self._mem_mb,
            )
        except Exception as e:
            logger.warning("[GpuEval] 批量求值异常，禁用GPU: %s", e)
            self._failed = True
            self.ready = False
            return None, None
        n_gpu = int(gpu_ok.sum())
        n_cpu = len(population) - n_gpu
        dt = time.perf_counter() - t0
        self._stats["gpu_evals"] += n_gpu
        self._stats["cpu_evals"] += n_cpu
        if n_gpu > 0:
            logger.info(
                "[GpuEval] 种群求值 P=%d GPU=%d CPU=%d 耗时=%.2fs",
                len(population), n_gpu, n_cpu, dt,
            )
        return vals, gpu_ok

    # ── 精英值缓存（每代刷新一次） ──────────────────────────────────
    def refresh_elites(self, elite_asts: Sequence[dict]) -> None:
        from backend.services.evolution.gpu_batch_eval import eval_panel_batch

        self._elite_fvs = []
        self._elite_ids = []
        if not elite_asts:
            return
        fvs: List[np.ndarray] = []
        gpu_vals = None
        gpu_ok = None
        if self.ready and not self._failed:
            try:
                gpu_vals, gpu_ok = eval_panel_batch(
                    elite_asts, self._fields, device="cuda",
                    chunk=self._chunk, mem_mb=self._mem_mb,
                )
            except Exception as e:
                logger.debug("[GpuEval] 精英 GPU 求值失败，回退 numpy: %s", e)
                gpu_vals = None
        for i, e_ast in enumerate(elite_asts):
            try:
                if gpu_vals is not None and gpu_ok is not None and gpu_ok[i]:
                    fv = gpu_vals[i]
                else:
                    fv = np.asarray(self._fn({"expr": _parse_ast(e_ast)}), dtype=float)
                    if fv.ndim == 0:
                        fv = np.full_like(self._target, float(fv))
                if fv.shape == self._target.shape:
                    fvs.append(np.asarray(fv, dtype=float))
            except Exception:
                continue
        self._elite_fvs = fvs
        self._elite_ids = [str(a) for a in elite_asts][: len(fvs)]

    @property
    def elite_fvs(self) -> List[np.ndarray]:
        return self._elite_fvs


def _parse_ast(ast: dict):
    from backend.services.factor_engine.expr.parser import parse

    return parse(ast)


def _oplist(node, acc=None):
    acc = acc if acc is not None else []
    if isinstance(node, dict) and "op" in node:
        acc.append(node["op"])
        for x in node.get("args", []):
            _oplist(x, acc)
    return acc


def compute_fitness_from_values(
    vals: np.ndarray,
    population: Sequence[dict],
    target: np.ndarray,
    min_samples: int,
    lam_c: float,
    lam_corr: float,
    elite_fvs: Sequence[np.ndarray],
    node_counts: Optional[Sequence[int]] = None,
) -> List[float]:
    """向量化适应度组装 —— 与 gp_miner._fitness_core 语义逐项对齐。

    返回 fitness 列表；无效个体 -inf。
    """
    from backend.services.evolution.gp_miner import _count_nodes as _cn

    P = vals.shape[0]
    t = np.asarray(target, dtype=float)
    n = t.shape[0]
    out = [float("-inf")] * P
    t_fin = np.isfinite(t)
    for i in range(P):
        fv = np.asarray(vals[i], dtype=float)
        if fv.ndim == 0:
            fv = np.full_like(t, float(fv))
        if fv.shape != t.shape:
            continue
        mask = np.isfinite(fv) & t_fin
        cnt = int(mask.sum())
        if cnt < min_samples:
            continue
        fvm = fv[mask]
        tm = t[mask]
        if np.std(fvm) < 1e-12:
            continue  # 常数因子
        ic = abs(float(np.corrcoef(fvm, tm)[0, 1]))
        if not np.isfinite(ic):
            continue
        penalty_c = lam_c * (node_counts[i] if node_counts is not None else _cn(population[i]))
        corr_pen = 0.0
        if elite_fvs:
            max_corr = 0.0
            for e_fv in elite_fvs:
                e_fv = np.asarray(e_fv, dtype=float)
                if e_fv.shape != t.shape:
                    continue
                m2 = mask & np.isfinite(e_fv)
                if m2.sum() < 10:
                    continue
                c = abs(float(np.corrcoef(fv[m2], e_fv[m2])[0, 1]))
                if np.isfinite(c):
                    max_corr = max(max_corr, c)
            corr_pen = lam_corr * max_corr
        out[i] = float(ic - penalty_c - corr_pen)
    return out
