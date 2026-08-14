"""
Pool-aware 因子挖掘（P4.1/1b/6，方案 §P4.1/1b/6 / §2.1.2 AlphaGen）。

目标：挖掘因子时奖励"对组合 IC 的边际贡献"（pool-aware），而非单因子 IC。
    - AlphaGen 式 RL（MaskablePPO + 动作掩码 token 生成 + 池感知奖励）接口
    - GP 多样性源（gplearn + ts 算子，parsimony）
    - Codegen（LLM 表达式生成 + critic 审计）

三者共享 alpha 池 + 筛选门槛（P1.2 purge_pipeline）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from backend.services.factor_engine.expr.audit import audit
from backend.services.factor_engine.expr.ops import LOOKAHEAD_BANNED_OPS, OP_REGISTRY
from backend.services.factor_engine.expr.parser import FactorExpr, parse

logger = logging.getLogger(__name__)


# ==================== Pool-aware 边际IC交叉验证（P1，规划文档§5.2） ====================
# 现有 try_admit 只用全样本两两相关性做冗余剔除，没有交叉验证，容易被单折
# 巧合的高相关"骗过"接纳一个实际不稳定的因子。这里补上 TimeSeriesSplit 版本：
# 不是看"加入后全样本IC变好没"，而是看"每个时间切片上，样本外的池子整体IC
# 是否稳定变好"——避免用同一份数据既选因子又验证因子的样本内偏差。
#
# v2.0文档提到的 PoolAwareReward+LassoCV 脚手架经核查不存在，边际IC计算逻辑
# 这里从零实现；权重方案沿用现有 AlphaPool.weights 的 sign(corr) 机制（而非引入
# 额外的Lasso回归），保持与现有池子加权逻辑一致、可控。

def _cv_ic(factor_matrix: np.ndarray, target: np.ndarray, tscv) -> float:
    """时序交叉验证IC：训练折上用符号相关性定权重，测试折(样本外)上算IC，取均值。"""
    mask_all = np.all(np.isfinite(factor_matrix), axis=1) & np.isfinite(target)
    if mask_all.sum() < 30:
        return 0.0
    fm = factor_matrix[mask_all]
    tgt = target[mask_all]
    ics: list[float] = []
    for train_idx, test_idx in tscv.split(fm):
        if len(train_idx) < 10 or len(test_idx) < 5:
            continue
        train_fm, train_tgt = fm[train_idx], tgt[train_idx]
        test_fm, test_tgt = fm[test_idx], tgt[test_idx]
        weights = []
        for k in range(train_fm.shape[1]):
            c = np.corrcoef(train_fm[:, k], train_tgt)[0, 1]
            weights.append(float(np.sign(c)) if np.isfinite(c) else 0.0)
        w = np.array(weights)
        if np.allclose(w, 0):
            continue
        combined_test = test_fm @ w
        if np.std(combined_test) < 1e-12:
            continue
        ic = np.corrcoef(combined_test, test_tgt)[0, 1]
        if np.isfinite(ic):
            ics.append(abs(float(ic)))
    return float(np.mean(ics)) if ics else 0.0


def compute_marginal_ic_cv(
    candidate: np.ndarray, pool: Optional[np.ndarray], forward_returns: np.ndarray,
    cv_folds: int = 5,
) -> float:
    """带时序交叉验证的边际IC：augmented(pool+candidate)的样本外IC - pool自身样本外IC。

    正值越大代表新因子在样本外真的为池子增量贡献了信息，而不是靠一次巧合的
    全样本相关性混进来。
    """
    from sklearn.model_selection import TimeSeriesSplit
    tscv = TimeSeriesSplit(n_splits=cv_folds)
    candidate = np.asarray(candidate, dtype=float).reshape(-1, 1)
    if pool is None or pool.size == 0:
        pool_ic = 0.0
        augmented = candidate
    else:
        pool = np.asarray(pool, dtype=float)
        pool_ic = _cv_ic(pool, forward_returns, tscv)
        augmented = np.column_stack([pool, candidate])
    augmented_ic = _cv_ic(augmented, forward_returns, tscv)
    return augmented_ic - pool_ic


@dataclass
class AlphaPool:
    """
    线性 alpha 池（AlphaGen 式）。

    新表达式仅当对池 IC 有边际贡献（L1 正则）才被接纳。
    """
    exprs: list[FactorExpr] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    capacity: int = 20
    l1_alpha: float = 5e-3
    ic_lower_bound: float = 0.02

    def try_admit(
        self, expr: FactorExpr, factor_values: np.ndarray,
        target: np.ndarray, pool_factor_matrix: Optional[np.ndarray] = None,
    ) -> tuple[bool, float]:
        """
        尝试接纳新表达式。返回 (是否接纳, 边际 IC 贡献)。

        pool-aware：仅当对池 IC 边际贡献 > eps 且增量相关 < 阈值才接纳。
        """
        if len(self.exprs) >= self.capacity:
            return False, 0.0
        # 单因子 IC
        tgt = np.asarray(target, dtype=float)
        fv = np.asarray(factor_values, dtype=float)
        # [2026-07-17 修复] 随机生成的 AST 有一定概率是纯常数叶子（无字段引用，
        # 例如 {"c": 5.0}），expr.evaluate 对常数表达式返回的是标量/0维数组而非
        # 与 K 线等长的序列。此前 fv[mask] 对 0 维数组按布尔数组索引会直接抛
        # "too many indices for array"，且这个异常没被 mine_random 内层捕获，
        # 导致一次随机命中常数就让整轮 100~2000 次挖掘全部中断——这才是"挖掘器
        # 100次尝试0命中"的真正原因，不是搜索次数不够。这里统一广播成等长数组，
        # 常数（零方差）因子本身也毫无预测力，直接拒绝即可。
        if fv.ndim == 0:
            fv = np.full_like(tgt, float(fv))
        elif fv.shape != tgt.shape:
            return False, 0.0
        mask = np.isfinite(fv) & np.isfinite(tgt)
        if mask.sum() < 10:
            return False, 0.0
        if np.std(fv[mask]) < 1e-12:
            return False, 0.0  # 常数因子，无区分力
        corr_new = float(np.corrcoef(fv[mask], tgt[mask])[0, 1])
        if not np.isfinite(corr_new) or abs(corr_new) < self.ic_lower_bound:
            return False, 0.0
        # 增量相关（与池内已有）——先做便宜的两两相关粗筛，明显冗余的直接拒绝，
        # 不用跑一遍更贵的交叉验证。
        # [2026-07-18 修复] 此前 mine_random 调 try_admit 时从未传 pool_factor_matrix
        # 参数（永远是默认值 None）——本函数从上线起这一整段增量相关检查就是死代码，
        # 挖掘器实际只按"单因子IC是否达标"接纳，从没真正检查过跟已有池子成员是否
        # 冗余。现在 mine_random 已改为真正构建并传入该矩阵（见下方修改）。
        if self.exprs and pool_factor_matrix is not None:
            for col in range(pool_factor_matrix.shape[1]):
                existing = pool_factor_matrix[:, col]
                m2 = np.isfinite(fv) & np.isfinite(existing)
                if m2.sum() < 10:
                    continue
                c = abs(float(np.corrcoef(fv[m2], existing[m2])[0, 1]))
                if c > 0.85:  # 高相关，不接纳
                    return False, 0.0

            # Pool-aware 边际IC交叉验证（规划文档§5.2，2026-07-18新增）：粗筛过了
            # 不代表真的有增量信息——用TimeSeriesSplit核实"加入后样本外池子IC
            # 是否真的变好"，而不是只看两两相关够不够低。样本不足做不了CV时
            # 退化为原始单因子IC（不因数据不够就一律拒绝，也不虚报CV通过）。
            mask_full = (
                np.all(np.isfinite(pool_factor_matrix), axis=1)
                & np.isfinite(fv) & np.isfinite(tgt)
            )
            if mask_full.sum() >= 30:
                try:
                    marginal = compute_marginal_ic_cv(
                        fv[mask_full], pool_factor_matrix[mask_full], tgt[mask_full],
                    )
                except Exception:
                    marginal = abs(corr_new)
                if marginal < self.l1_alpha:
                    return False, 0.0
        # 接纳
        self.exprs.append(expr)
        self.weights.append(float(np.sign(corr_new)))
        return True, abs(corr_new)

    def pool_ic(self, factor_matrix: np.ndarray, target: np.ndarray) -> float:
        """池整体 IC（加权线性组合 vs target）。"""
        if not self.exprs:
            return 0.0
        w = np.array(self.weights)
        combined = factor_matrix @ w
        mask = np.isfinite(combined) & np.isfinite(target)
        if mask.sum() < 10:
            return 0.0
        return abs(float(np.corrcoef(combined[mask], target[mask])[0, 1]))

    def size(self) -> int:
        return len(self.exprs)


# ==================== Pool-aware 挖掘接口 ====================

@dataclass
class MiningConfig:
    """挖掘配置。"""
    max_expr_depth: int = 5
    pool_capacity: int = 20
    n_candidates: int = 100


class AlphaMiner:
    """
    Pool-aware 因子挖掘器。

    生产：AlphaGen MaskablePPO（RL 挖掘）。
    当前：随机表达式生成 + pool 准入（验证 pool-aware 逻辑）。
    """

    def __init__(self, pool: AlphaPool, config: MiningConfig | None = None):
        self.pool = pool
        self.config = config or MiningConfig()
        self._rng = np.random.default_rng()

    def mine_random(
        self, fields: list[str], factor_value_fn: Callable[[dict], np.ndarray],
        target: np.ndarray, max_attempts: int | None = None,
    ) -> list[tuple[FactorExpr, float]]:
        """
        随机生成表达式并尝试入池（pool-aware）。
        返回被接纳的 (expr, 边际贡献) 列表。
        """
        admitted: list[tuple[FactorExpr, float]] = []
        attempts = max_attempts or self.config.n_candidates
        # [2026-08-14 P1-G1] 剔除单序列前视算子（rank/cs_rank/scale 已被 audit 禁）
        op_names = [n for n in OP_REGISTRY.keys() if n not in LOOKAHEAD_BANNED_OPS]
        tgt_arr = np.asarray(target, dtype=float)
        # [2026-07-18 修复] 此前这里调 try_admit 从不传 pool_factor_matrix，
        # AlphaPool 里"增量相关/边际IC"整段准入逻辑因此从未真正执行过——挖掘器
        # 实际只按单因子IC接纳，池子里可能全是高度相关的冗余因子。现在维护一份
        # 已入池因子值的缓存（随录取增量追加，不重复evaluate），每次尝试都真正
        # 传入当前池子矩阵。
        pool_values_cache: list[np.ndarray] = []
        for _ in range(attempts):
            ast = self._random_ast(fields, op_names, depth=0)
            if ast is None:
                continue
            result = audit(ast)
            if not result.ok:
                continue
            try:
                expr = parse(ast)
            except Exception:
                continue
            try:
                fv = factor_value_fn({"expr": expr})
            except Exception:
                continue
            pool_matrix = np.column_stack(pool_values_cache) if pool_values_cache else None
            # 防御性 try/except：即便 try_admit 内部再出现未预料的 shape/数值异常，
            # 也只跳过这一个候选，不能让单次坏样本掐死整轮挖掘（历史教训见上）。
            try:
                ok, contribution = self.pool.try_admit(
                    expr, fv, target, pool_factor_matrix=pool_matrix,
                )
            except Exception:
                continue
            if ok:
                admitted.append((expr, contribution))
                fv_arr = np.asarray(fv, dtype=float)
                if fv_arr.ndim == 0:
                    fv_arr = np.full_like(tgt_arr, float(fv_arr))
                pool_values_cache.append(fv_arr)
        return admitted

    def mine_llm_candidates(
        self, fields: list[str], factor_value_fn: Callable[[dict], np.ndarray],
        target: np.ndarray, prompt: str, n_candidates: int = 8,
        critic: Optional["CodegenCritic"] = None,
    ) -> list[tuple[FactorExpr, float]]:
        """
        LLM 因子生成 + 打分入池（pool-aware）。

        [2026-08-06 2.5] 接线：CodegenCritic 调 LLM 生成表达式 AST → audit
        拦截 look-ahead → try_admit 按边际 IC 贡献打分入池。LLM 配置/调用
        不可用时不产生任何候选（显式降级），不再有占位假表达式。
        """
        critic = critic or CodegenCritic()
        admitted: list[tuple[FactorExpr, float]] = []
        pool_values_cache: list[np.ndarray] = []
        for i in range(int(n_candidates)):
            res = critic.generate_and_audit(
                prompt,
                existing_pool_exprs=[e.ast for e in self.pool.exprs],
            )
            if not res.audit_passed or not res.expr_ast:
                if i == 0:
                    logger.warning(
                        f"[AlphaMiner] LLM 因子生成不可用（第1个候选即失败）: {res.reason}"
                    )
                continue
            try:
                expr = parse(res.expr_ast)
            except Exception as e:
                logger.warning(f"[AlphaMiner] LLM 候选解析失败: {e}")
                continue
            try:
                fv = factor_value_fn({"expr": expr})
            except Exception:
                continue
            pool_matrix = np.column_stack(pool_values_cache) if pool_values_cache else None
            try:
                ok, contribution = self.pool.try_admit(
                    expr, fv, target, pool_factor_matrix=pool_matrix,
                )
            except Exception:
                continue
            if ok:
                admitted.append((expr, contribution))
                fv_arr = np.asarray(fv, dtype=float)
                if fv_arr.ndim == 0:
                    fv_arr = np.full_like(np.asarray(target, dtype=float), float(fv_arr))
                pool_values_cache.append(fv_arr)
        return admitted

    def _random_ast(self, fields: list[str], op_names: list[str], depth: int) -> Optional[dict]:
        """随机生成表达式 AST（深度受限）。"""
        if depth >= self.config.max_expr_depth or self._rng.random() < 0.3:
            # 叶子：字段或常量
            if self._rng.random() < 0.7 and fields:
                return {"f": str(self._rng.choice(fields))}
            return {"c": float(self._rng.choice([1, 3, 5, 10, 20]))}
        op = str(self._rng.choice(op_names))
        if op not in OP_REGISTRY:
            return None
        arity, _ = OP_REGISTRY[op]
        args = []
        for _ in range(arity):
            child = self._random_ast(fields, op_names, depth + 1)
            if child is None:
                return None
            args.append(child)
        return {"op": op, "args": args}


# ==================== Codegen（LLM 因子生成 + critic） ====================

@dataclass
class CodegenResult:
    """LLM 代码生成结果。"""
    expr_ast: Optional[dict]
    audit_passed: bool
    reason: str = ""


class CodegenCritic:
    """
    LLM 因子表达式生成 + critic 审计（AlphaEvolve 风）。

    [2026-08-06 2.5 接线] 此前是占位（_has_llm=False，硬编码返回一个示例
    表达式假装 audit 通过），会造成假候选。现已接线真实 LLM：
      - 配置源：get_llm_config_for_usage("factor_mining")（账户/租户级，
        无配置即显式降级，绝不产生假表达式）
      - 生成：prompt → LLM 输出 JSON AST → 解析 → audit 拦截 look-ahead
      - 消费：AlphaMiner.mine_llm_candidates 按边际 IC 打分入池
    依赖说明：需要环境已配置 factor_mining 用途的 LLM key；sidecar
    （opencode）恢复后可顺带提升生成质量（阶段 6，不阻塞本接线）。
    """

    def __init__(self):
        self._config = None
        self._config_loaded = False

    # ── LLM 配置（惰性加载，每次调用重试，配置后即生效） ──
    def _load_config(self):
        """取 factor_mining 用途的 LLM 配置。

        [2026-08-06 2.5] 进化链是后台线程，无 account_id/HTTP 身份：
        显式解析管理员租户 + set_request_identity 穿透 RLS（模式同
        coin_select_platform_service），否则 llm_configurations 被 RLS
        滤空 → 永远降级。无管理员租户/无配置时返回 None（显式降级）。
        """
        try:
            from backend.services.llm_config_service import (
                get_llm_config, get_llm_config_for_usage,
            )
            from backend.services.coin_select_platform_service import (
                resolve_admin_tenant_id,
            )
            tid = resolve_admin_tenant_id()
            if not tid:
                return None
            try:
                from backend.core.tenant import set_request_identity
                set_request_identity(int(tid), "admin")
            except Exception as e:
                logger.warning(f"[CodegenCritic] set_request_identity({tid}): {e}")
            cfg = get_llm_config_for_usage("factor_mining", tenant_id=tid, tier="deep")
            if not (cfg and getattr(cfg, "api_key", None)):
                cfg = get_llm_config_for_usage("factor_mining", tenant_id=tid, tier="fast")
            if not (cfg and getattr(cfg, "api_key", None)):
                cfg = get_llm_config(tier="deep", tenant_id=tid)
            if not (cfg and getattr(cfg, "api_key", None)):
                return None
            return cfg
        except Exception as e:
            logger.warning(f"[CodegenCritic] LLM 配置解析失败: {e}")
            return None

    @staticmethod
    def _parse_ast(raw: str) -> Optional[dict]:
        """解析 LLM 输出的 JSON AST（容错 markdown 代码块/前后缀）。"""
        if not raw:
            return None
        text = str(raw).strip()
        # 去掉 ```json ... ``` 代码块围栏
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().lower().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 取第一个 { 到最后一个 } 再试一次（LLM 常夹带解释文字）
            s, e = text.find("{"), text.rfind("}")
            if s < 0 or e <= s:
                return None
            try:
                data = json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                return None
        if isinstance(data, dict) and ("op" in data or "f" in data or "c" in data):
            return data
        if isinstance(data, dict) and "expr_ast" in data:
            inner = data["expr_ast"]
            if isinstance(inner, dict):
                return inner
        return None

    def generate_and_audit(
        self, prompt: str, existing_pool_exprs: list[dict] | None = None,
    ) -> CodegenResult:
        """
        LLM 生成表达式 + critic 审计。

        返回的 audit_passed=True 的表达式才进 DRAFT（P1.3 生命周期）。
        LLM 不可用/输出不合法 → audit_passed=False + reason（显式降级）。
        """
        config = self._load_config()
        if not config or not getattr(config, "api_key", None):
            return CodegenResult(
                expr_ast=None, audit_passed=False,
                reason="llm_config_unavailable (factor_mining 无 LLM 配置)",
            )
        try:
            from backend.services.llm_config_service import call_llm_api_sync
        except Exception as e:
            return CodegenResult(expr_ast=None, audit_passed=False, reason=f"import_error: {e}")

        pool_hint = ""
        if existing_pool_exprs:
            try:
                pool_hint = (
                    "\n已有池内表达式（避免生成重复/高度相似候选）:\n"
                    + json.dumps(existing_pool_exprs[:10], ensure_ascii=False)
                )
            except Exception:
                pool_hint = ""
        messages = [
            {"role": "system", "content": (
                "你是量化因子表达式生成器。输出一个 JSON 因子表达式 AST，"
                "节点格式：{\"f\": \"字段名\"} 或 {\"c\": 数值} 或 "
                "{\"op\": \"算子名\", \"args\": [子节点...]}。"
                "可参考的 ref 算子：{\"op\": \"ref\", \"args\": [{\"f\": \"close\"}, {\"c\": 5}]}。"
                "禁止使用未来数据（look-ahead）：不得引用未实现的字段或"
                "跨越当前 bar 的窗口计算。只输出 JSON，不要解释。"
            )},
            {"role": "user", "content": f"{prompt}{pool_hint}"},
        ]
        try:
            resp_data = call_llm_api_sync(
                config,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.6,
                caller="codegen_critic",
            )
        except Exception as e:
            return CodegenResult(expr_ast=None, audit_passed=False, reason=f"llm_error: {e}")
        resp = None
        if resp_data:
            choices = resp_data.get("choices") or []
            if choices:
                resp = (choices[0].get("message") or {}).get("content")
        if not resp:
            return CodegenResult(expr_ast=None, audit_passed=False, reason="llm_empty_response")
        ast = self._parse_ast(resp)
        if ast is None:
            return CodegenResult(expr_ast=None, audit_passed=False, reason="llm_invalid_ast")
        result = audit(ast)
        if result.ok:
            # [2026-08-13 P1-10] 换手上限拦截：look-ahead 之外的第二道静态门
            _ok_turn, _reason_turn = self._audit_turnover_cap(ast)
            if not _ok_turn:
                return CodegenResult(expr_ast=None, audit_passed=False, reason=_reason_turn)
        return CodegenResult(
            expr_ast=ast if result.ok else None,
            audit_passed=result.ok,
            reason="OK" if result.ok else "; ".join(result.errors),
        )

    @staticmethod
    def _audit_turnover_cap(ast: dict) -> tuple[bool, str]:
        """[2026-08-13 P1-10] 换手上限拦截（静态代理）。

        收集 AST 内所有窗口/延迟类算子的窗口参数（args[-1] 常量），
        最短窗口 < CODEGEN_MIN_TURNOVER_WINDOW（默认 2 根）说明信号每
        1-2 根 K 线即重算/翻转 → 换手过高，taker+funding 成本会吃掉
        alpha。env CODEGEN_MIN_TURNOVER_WINDOW=0 显式关闭。
        """
        try:
            _min_win = int(os.getenv("CODEGEN_MIN_TURNOVER_WINDOW", "2") or 2)
        except (TypeError, ValueError):
            _min_win = 2
        if _min_win <= 0:
            return True, "OK"  # 显式关闭
        _WINDOW_OPS = frozenset({
            "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank",
            "delta", "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin",
            "ts_corr", "corr", "cov",
        })
        windows: list[float] = []

        def _walk(node):
            if not isinstance(node, dict):
                return
            args = node.get("args", []) if "op" in node else []
            if "op" in node and node["op"] in _WINDOW_OPS and isinstance(args, list) and args:
                last = args[-1]
                val = None
                if isinstance(last, (int, float)):
                    val = last
                elif isinstance(last, dict) and "c" in last and isinstance(last["c"], (int, float)):
                    val = last["c"]
                if val is not None and val > 0:
                    windows.append(float(val))
            for ch in args:
                _walk(ch)

        _walk(ast)
        if windows and min(windows) < _min_win:
            return False, (
                f"turnover_cap: 最短窗口 {min(windows):.0f} 根 < 下限 {_min_win} 根，"
                f"换手过高（taker+funding 成本会吃掉 alpha）"
            )
        return True, "OK"

    def reject_lookahead(self, ast: dict) -> tuple[bool, str]:
        """critic 检测 look-ahead bias。"""
        result = audit(ast)
        if not result.ok:
            return False, "; ".join(result.errors)
        return True, "OK"
