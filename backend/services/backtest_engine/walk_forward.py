"""
ATAS V2 Walk-Forward分析

提供滚动窗口回测，防止过拟合
"""
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
import pandas as pd
import numpy as np

from .backtest_engine import BacktestEngine, BacktestConfig, BacktestResult, Strategy
from .loss_functions import get_loss

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v else default


@dataclass
class WalkForwardConfig:
    """Walk-Forward配置"""
    train_period_days: int = 252  # 训练期（天）
    test_period_days: int = 63  # 测试期（天）
    step_days: int = 21  # 滚动步长（天）
    
    # 优化参数
    optimize_on_train: bool = True  # 是否在训练集上优化
    optimization_metric: str = 'sharpe_ratio'  # 优化目标指标
    
    # 回测配置
    backtest_config: Optional[BacktestConfig] = None

    # ===== 整改#1 新增（默认值等价旧行为；env 未设时 purge/embargo=0、optimizer=grid）=====
    # env 开关（doc §整改#1）：WFO_PURGE_DAYS / WFO_EMBARGO_DAYS / WFO_OPTIMIZER / WFO_RUN_CSCV。
    # 推荐生产值 purge_days=5, embargo_days=3, optimizer=optuna；env 未设则保持旧行为。
    purge_days: int = field(default_factory=lambda: _env_int("WFO_PURGE_DAYS", 0))
    embargo_days: int = field(default_factory=lambda: _env_int("WFO_EMBARGO_DAYS", 0))
    # 损失/目标（loss registry key）：sharpe|sortino|calmar|max_drawdown|profit_factor|sharpe_dd|ulcer
    loss_function: str = field(default_factory=lambda: _env_str("WFO_LOSS_FUNCTION", "sharpe"))
    optimization_metrics: Optional[List[str]] = None   # 预留多目标
    # 优化器：'grid'（默认，穷举）|'optuna'（TPE，缺失自动回退 grid）|'cma_es'（暂回退 grid）
    optimizer: str = field(default_factory=lambda: _env_str("WFO_OPTIMIZER", "grid"))
    n_optuna_trials: int = field(default_factory=lambda: _env_int("WFO_N_OPTUNA_TRIALS", 100))
    # 过拟合诊断：DSR/PSR/MinBTL 很便宜，默认开（仅增报告，不改交易行为）。
    # [2026-07-18] CSCV/PBO 默认由 False 改为 True（方案 P0 "WFO硬门"）——但诚实说明：
    # 本类 `WalkForwardOptimizer.run()` 目前在全代码库没有任何调用点（孤立脚手架，
    # 未接入任何策略优化/回测触发路径），改这个默认值本身不会改变任何当前实际运行
    # 行为。真正生效的过拟合硬门在 factor_evolution_loop(因子DSR/PBO)、
    # strategy_evolver.py(PBO_AUDIT_ENABLED=true，见 .env) 与
    # promotion_gate_service.py(PROMOTION_GATE_ENABLED=true) 三处，均已核实为真实生效的硬门。
    run_dsr: bool = field(default_factory=lambda: _env_bool("WFO_RUN_DSR", True))
    run_cscv: bool = field(default_factory=lambda: _env_bool("WFO_RUN_CSCV", True))
    cscv_n_blocks: int = field(default_factory=lambda: _env_int("WFO_CSCV_N_BLOCKS", 16))
    # IC 衰减耦合（预留）
    decay_aware_stepping: bool = False
    decay_halflife_source: str = 'factor_evaluator'


@dataclass
class WalkForwardPeriod:
    """Walk-Forward时期"""
    period_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    
    train_result: Optional[BacktestResult] = None
    test_result: Optional[BacktestResult] = None
    optimized_params: Optional[Dict[str, Any]] = None

    # 整改#1：purge/embargo 边界（便于审计与可视化）
    purge_end: Optional[datetime] = None
    embargo_end: Optional[datetime] = None


@dataclass
class WalkForwardResult:
    """Walk-Forward分析结果"""
    periods: List[WalkForwardPeriod]
    
    # 总体统计
    total_train_return: float
    total_test_return: float  # 样本外收益（最重要）
    
    # 稳定性指标
    test_sharpe_ratio: float
    test_max_drawdown: float
    
    # 一致性分析
    consistency_score: float  # 训练集和测试集表现一致性
    overfitting_score: float  # 过拟合评分
    
    # 详细数据
    test_equity_curve: pd.Series  # 测试集权益曲线（拼接）
    period_returns: pd.DataFrame  # 各期收益对比

    # 整改#1：研究级过拟合诊断（可选填充）
    pbo: Optional[float] = None                      # Probability of Backtest Overfitting
    pbo_verdict: str = ""                            # 'robust'|'borderline'|'overfit'
    deflated_sharpe: Optional[float] = None          # DSR ∈ [0,1]
    probabilistic_sharpe: Optional[float] = None     # PSR ∈ [0,1]
    min_required_length_years: Optional[float] = None
    purge_embargo_applied: bool = False


class WalkForwardAnalyzer:
    """Walk-Forward分析器"""
    
    def __init__(self, config: Optional[WalkForwardConfig] = None):
        self.config = config or WalkForwardConfig()
        if not self.config.backtest_config:
            self.config.backtest_config = BacktestConfig()
    
    def analyze(
        self,
        strategy_factory: Callable[[Dict[str, Any]], Strategy],
        data: pd.DataFrame,
        param_grid: Optional[Dict[str, List[Any]]] = None
    ) -> WalkForwardResult:
        """
        执行Walk-Forward分析
        
        Args:
            strategy_factory: 策略工厂函数，接收参数返回策略实例
            data: 完整历史数据
            param_grid: 参数网格（用于优化）
            
        Returns:
            WalkForwardResult: 分析结果
        """
        # 生成时期划分
        periods = self._generate_periods(data.index[0], data.index[-1])
        
        # 逐期进行训练和测试
        for period in periods:
            # 获取训练集和测试集数据
            train_data = data[
                (data.index >= period.train_start) &
                (data.index <= period.train_end)
            ]
            test_data = data[
                (data.index >= period.test_start) &
                (data.index <= period.test_end)
            ]
            
            if len(train_data) == 0 or len(test_data) == 0:
                continue
            
            # 在训练集上优化参数
            if self.config.optimize_on_train and param_grid:
                best_params = self._optimize_params(
                    strategy_factory,
                    train_data,
                    param_grid
                )
                period.optimized_params = best_params
            else:
                best_params = {}
                period.optimized_params = {}
            
            # 在训练集上回测
            strategy = strategy_factory(best_params)
            engine = BacktestEngine(self.config.backtest_config)
            period.train_result = engine.run(strategy, train_data)
            
            # 在测试集上回测（使用相同参数）
            strategy = strategy_factory(best_params)
            engine = BacktestEngine(self.config.backtest_config)
            period.test_result = engine.run(strategy, test_data)
        
        # 计算总体统计
        result = self._calculate_overall_stats(periods)

        # 整改#1：附加过拟合诊断（不改变交易行为，仅增报告）
        result.purge_embargo_applied = bool(self.config.purge_days > 0 or self.config.embargo_days > 0)
        n_trials = self._estimate_n_trials(param_grid)
        if self.config.run_dsr:
            try:
                diag = self._compute_overfitting_diagnostics(result.periods, n_trials)
                result.deflated_sharpe = diag.get("dsr")
                result.probabilistic_sharpe = diag.get("psr")
                result.min_required_length_years = diag.get("min_len_years")
            except Exception as e:  # noqa: BLE001 —— 诊断失败不影响主结果
                print(f"[WFO] 过拟合诊断失败（忽略）: {e}")

        # 整改#1：CSCV/PBO（较贵，默认关）。开启后填充 result.pbo，
        # 调度器可据此拒绝晋升（如 PBO>0.5 判过拟合）；此处仅计算不强制门禁。
        if self.config.run_cscv and param_grid:
            try:
                pbo_res = self._compute_pbo(strategy_factory, data, param_grid, result.periods)
                if pbo_res is not None:
                    result.pbo = pbo_res.pbo
                    result.pbo_verdict = pbo_res.verdict
            except Exception as e:  # noqa: BLE001
                print(f"[WFO] CSCV/PBO 计算失败（忽略）: {e}")
        return result

    def _compute_pbo(
        self,
        strategy_factory: Callable[[Dict[str, Any]], Strategy],
        data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        periods: List[WalkForwardPeriod],
    ):
        """构建 (n_combos × n_periods) 的 IS/OOS 收益矩阵并算 PBO。

        为每个参数组合在每期的 train(IS)/test(OOS) 上回测，取 total_return 作为该
        (策略, 期) 的表现，喂 compute_pbo_cscv。较贵（组合数×期数×2 次回测），
        故仅在 run_cscv=True 时触发。
        """
        from .overfitting_metrics import compute_pbo_cscv

        combos = self._generate_param_combinations(param_grid)
        if len(combos) < 2 or len(periods) < 2:
            return None

        is_matrix: List[List[float]] = []
        oos_matrix: List[List[float]] = []
        for params in combos:
            is_row: List[float] = []
            oos_row: List[float] = []
            for period in periods:
                train_data = data[(data.index >= period.train_start) & (data.index <= period.train_end)]
                test_data = data[(data.index >= period.test_start) & (data.index <= period.test_end)]
                try:
                    r_is = BacktestEngine(self.config.backtest_config).run(strategy_factory(params), train_data)
                    r_oos = BacktestEngine(self.config.backtest_config).run(strategy_factory(params), test_data)
                    is_row.append(float(r_is.total_return))
                    oos_row.append(float(r_oos.total_return))
                except Exception:  # noqa: BLE001
                    is_row.append(0.0)
                    oos_row.append(0.0)
            is_matrix.append(is_row)
            oos_matrix.append(oos_row)

        # compute_pbo_cscv 内部会按拼接后时间长度自适应封顶 n_blocks
        return compute_pbo_cscv(
            np.asarray(is_matrix), np.asarray(oos_matrix), n_blocks=self.config.cscv_n_blocks
        )

    def _estimate_n_trials(self, param_grid: Optional[Dict[str, List[Any]]]) -> int:
        """估计多重检验强度：grid=组合数；optuna=n_optuna_trials；无网格=1。"""
        if not param_grid:
            return 1
        if (self.config.optimizer or "grid").lower() == "optuna":
            return max(1, int(self.config.n_optuna_trials))
        try:
            return max(1, len(self._generate_param_combinations(param_grid)))
        except Exception:  # noqa: BLE001
            return 1
    
    def _generate_periods(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> List[WalkForwardPeriod]:
        """生成Walk-Forward时期划分"""
        periods = []
        period_id = 1
        
        current_train_start = start_date
        purge_days = max(0, int(self.config.purge_days))
        embargo_days = max(0, int(self.config.embargo_days))
        
        while True:
            # 计算训练期和测试期（整改#1：插入 purge + embargo 间隙）
            # purge=embargo=0 时与旧行为逐字等价：test_start=train_end+1, 边界用 test_end。
            train_start = current_train_start
            train_end = train_start + timedelta(days=self.config.train_period_days)
            purge_end = train_end + timedelta(days=purge_days)
            test_start = purge_end + timedelta(days=1)
            test_end = test_start + timedelta(days=self.config.test_period_days)
            embargo_end = test_end + timedelta(days=embargo_days)
            
            # 检查是否超出数据范围（含 embargo 预留区）
            if embargo_end > end_date:
                break
            
            periods.append(WalkForwardPeriod(
                period_id=period_id,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                purge_end=purge_end,
                embargo_end=embargo_end,
            ))
            
            # 滚动到下一期
            current_train_start += timedelta(days=self.config.step_days)
            period_id += 1
        
        return periods
    
    def _optimize_params(
        self,
        strategy_factory: Callable[[Dict[str, Any]], Strategy],
        train_data: pd.DataFrame,
        param_grid: Dict[str, List[Any]]
    ) -> Dict[str, Any]:
        """在训练集上优化参数（整改#1：optimizer 路由，默认 grid）。"""
        optimizer = (self.config.optimizer or "grid").strip().lower()
        if optimizer == "optuna":
            try:
                return self._optimize_optuna(strategy_factory, train_data, param_grid)
            except Exception as e:  # noqa: BLE001 —— optuna 缺失/异常自动回退穷举
                print(f"[WFO] optuna 优化失败，回退 grid: {e}")
                return self._optimize_grid(strategy_factory, train_data, param_grid)
        if optimizer == "cma_es":
            # CMA-ES 暂未实现（对应整改#20），回退 grid，保持可用
            # [2026-08-15] print → logger.warning（结构化告警，运维可见）
            logger.warning("[WFO] optimizer=cma_es 暂未实现，回退 grid（整改#20）")
            return self._optimize_grid(strategy_factory, train_data, param_grid)
        return self._optimize_grid(strategy_factory, train_data, param_grid)

    def _optimize_grid(
        self,
        strategy_factory: Callable[[Dict[str, Any]], Strategy],
        train_data: pd.DataFrame,
        param_grid: Dict[str, List[Any]]
    ) -> Dict[str, Any]:
        """穷举网格搜索（原逻辑）。"""
        best_params = {}
        best_score = float('-inf')
        
        # 生成参数组合
        param_combinations = self._generate_param_combinations(param_grid)
        
        # 遍历参数组合
        for params in param_combinations:
            try:
                strategy = strategy_factory(params)
                engine = BacktestEngine(self.config.backtest_config)
                result = engine.run(strategy, train_data)
                
                # 获取优化指标（走 loss registry）
                score = self._get_metric_value(result, self.config.loss_function or self.config.optimization_metric)
                
                if score > best_score:
                    best_score = score
                    best_params = params.copy()
            
            except Exception as e:
                print(f"Error testing params {params}: {e}")
                continue
        
        return best_params

    def _optimize_optuna(
        self,
        strategy_factory: Callable[[Dict[str, Any]], Strategy],
        train_data: pd.DataFrame,
        param_space: Dict[str, List[Any]]
    ) -> Dict[str, Any]:
        """用 Optuna TPE 在离散参数空间上搜索（对标 Freqtrade hyperopt）。

        param_space 复用 grid 的 {key: [候选值,...]} 格式，用 suggest_categorical 采样。
        optuna 未安装时由 _optimize_params 捕获异常并回退 grid。
        """
        import optuna  # 惰性导入；缺失则抛 ImportError 由上层回退

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        keys = list(param_space.keys())
        loss_key = self.config.loss_function or self.config.optimization_metric

        def objective(trial):
            params = {k: trial.suggest_categorical(k, list(param_space[k])) for k in keys}
            strategy = strategy_factory(params)
            engine = BacktestEngine(self.config.backtest_config)
            result = engine.run(strategy, train_data)
            return self._get_metric_value(result, loss_key)

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        study.optimize(objective, n_trials=max(1, int(self.config.n_optuna_trials)), show_progress_bar=False)
        return dict(study.best_params)

    def _compute_overfitting_diagnostics(self, periods: List[WalkForwardPeriod], n_trials: int) -> Dict[str, Any]:
        """整改#1：从拼接的 OOS 收益序列算 DSR/PSR/MinBTL。"""
        from .overfitting_metrics import (
            deflated_sharpe_ratio,
            probabilistic_sharpe_ratio,
            min_backtest_length,
            _safe_skew,
            _safe_kurt,
        )

        oos_chunks: List[np.ndarray] = []
        for p in periods:
            ec = getattr(p.test_result, "equity_curve", None)
            if ec is not None and len(ec) > 1:
                r = ec.pct_change().dropna().values
                if r.size:
                    oos_chunks.append(np.asarray(r, dtype=float))
        if not oos_chunks:
            return {}
        oos = np.concatenate(oos_chunks)
        oos = oos[np.isfinite(oos)]
        if oos.size < 3:
            return {}

        sd = float(np.std(oos, ddof=1))
        sr = float(np.mean(oos) / sd) if sd > 1e-12 else 0.0
        sk = _safe_skew(oos)
        ku = _safe_kurt(oos)
        psr = probabilistic_sharpe_ratio(sr, n=oos.size, benchmark_sharpe=0.0, skew=sk, kurt=ku)
        dsr, _ = deflated_sharpe_ratio(sr, max(int(n_trials), 1), oos, skew=sk, kurt=ku)
        mbtl = min_backtest_length(sr, skew=sk, kurt=ku)
        return {"psr": psr, "dsr": dsr, "min_len_years": mbtl}
    
    def _generate_param_combinations(
        self,
        param_grid: Dict[str, List[Any]]
    ) -> List[Dict[str, Any]]:
        """生成参数组合"""
        import itertools
        
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        
        combinations = []
        for combination in itertools.product(*values):
            param_dict = dict(zip(keys, combination))
            combinations.append(param_dict)
        
        return combinations
    
    def _get_metric_value(self, result: BacktestResult, metric: str) -> float:
        """获取指标值（整改#1：统一走 loss registry，越大越好）。"""
        return get_loss(metric)(result)
    
    def _calculate_overall_stats(
        self,
        periods: List[WalkForwardPeriod]
    ) -> WalkForwardResult:
        """计算总体统计"""
        # 过滤掉没有结果的时期
        valid_periods = [p for p in periods if p.train_result and p.test_result]
        
        if not valid_periods:
            raise ValueError("No valid periods found")
        
        # 计算总体收益
        train_returns = [p.train_result.total_return for p in valid_periods]
        test_returns = [p.test_result.total_return for p in valid_periods]
        
        total_train_return = np.mean(train_returns)
        total_test_return = np.mean(test_returns)
        
        # 拼接测试集权益曲线
        test_equity_curves = []
        for period in valid_periods:
            test_equity_curves.append(period.test_result.equity_curve)
        
        test_equity_curve = pd.concat(test_equity_curves)
        
        # 计算测试集统计
        test_sharpe_ratios = [p.test_result.sharpe_ratio for p in valid_periods]
        test_max_drawdowns = [p.test_result.max_drawdown for p in valid_periods]
        
        test_sharpe_ratio = np.mean(test_sharpe_ratios)
        test_max_drawdown = np.max(test_max_drawdowns)
        
        # 计算一致性评分
        consistency_score = self._calculate_consistency(valid_periods)
        
        # 计算过拟合评分
        overfitting_score = self._calculate_overfitting_score(valid_periods)
        
        # 构建期间收益对比DataFrame
        period_returns = pd.DataFrame({
            'period_id': [p.period_id for p in valid_periods],
            'train_return': [p.train_result.total_return for p in valid_periods],
            'test_return': [p.test_result.total_return for p in valid_periods],
            'train_sharpe': [p.train_result.sharpe_ratio for p in valid_periods],
            'test_sharpe': [p.test_result.sharpe_ratio for p in valid_periods],
        })
        
        return WalkForwardResult(
            periods=valid_periods,
            total_train_return=total_train_return,
            total_test_return=total_test_return,
            test_sharpe_ratio=test_sharpe_ratio,
            test_max_drawdown=test_max_drawdown,
            consistency_score=consistency_score,
            overfitting_score=overfitting_score,
            test_equity_curve=test_equity_curve,
            period_returns=period_returns
        )
    
    def _calculate_consistency(self, periods: List[WalkForwardPeriod]) -> float:
        """
        计算训练集和测试集表现的一致性
        
        Returns:
            float: 一致性评分 (0-1，越高越好)
        """
        train_returns = np.array([p.train_result.total_return for p in periods])
        test_returns = np.array([p.test_result.total_return for p in periods])
        
        # 计算相关系数
        if len(train_returns) > 1:
            correlation = np.corrcoef(train_returns, test_returns)[0, 1]
            # 将相关系数转换为0-1范围
            consistency = (correlation + 1) / 2
        else:
            consistency = 0.5
        
        return consistency
    
    def _calculate_overfitting_score(self, periods: List[WalkForwardPeriod]) -> float:
        """
        计算过拟合评分
        
        Returns:
            float: 过拟合评分 (0-1，越低越好)
        """
        train_returns = np.array([p.train_result.total_return for p in periods])
        test_returns = np.array([p.test_result.total_return for p in periods])
        
        # 计算训练集和测试集收益的差异
        mean_train = np.mean(train_returns)
        mean_test = np.mean(test_returns)
        
        if mean_train > 0:
            # 归一化差异
            overfitting = max(0, (mean_train - mean_test) / mean_train)
        else:
            overfitting = 1.0
        
        return min(overfitting, 1.0)
    
    def plot_results(self, result: WalkForwardResult, save_path: Optional[str] = None):
        """
        绘制Walk-Forward分析结果
        
        Args:
            result: 分析结果
            save_path: 保存路径（可选）
        """
        try:
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(3, 1, figsize=(12, 10))
            
            # 1. 权益曲线
            axes[0].plot(result.test_equity_curve.index, result.test_equity_curve.values)
            axes[0].set_title('Walk-Forward Test Equity Curve')
            axes[0].set_xlabel('Date')
            axes[0].set_ylabel('Equity')
            axes[0].grid(True)
            
            # 2. 期间收益对比
            x = result.period_returns['period_id']
            axes[1].bar(x - 0.2, result.period_returns['train_return'], width=0.4, label='Train', alpha=0.7)
            axes[1].bar(x + 0.2, result.period_returns['test_return'], width=0.4, label='Test', alpha=0.7)
            axes[1].set_title('Period Returns Comparison')
            axes[1].set_xlabel('Period')
            axes[1].set_ylabel('Return')
            axes[1].legend()
            axes[1].grid(True)
            
            # 3. 夏普比率对比
            axes[2].bar(x - 0.2, result.period_returns['train_sharpe'], width=0.4, label='Train', alpha=0.7)
            axes[2].bar(x + 0.2, result.period_returns['test_sharpe'], width=0.4, label='Test', alpha=0.7)
            axes[2].set_title('Period Sharpe Ratio Comparison')
            axes[2].set_xlabel('Period')
            axes[2].set_ylabel('Sharpe Ratio')
            axes[2].legend()
            axes[2].grid(True)
            
            plt.tight_layout()
            
            if save_path:
                plt.savefig(save_path, dpi=300, bbox_inches='tight')
            else:
                plt.show()
        
        except ImportError:
            print("matplotlib is required for plotting")
