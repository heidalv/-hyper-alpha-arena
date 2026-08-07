"""
Backtest Engine Module
"""
from backend.services.backtest_engine.backtest_engine import BacktestEngine, BacktestConfig, BacktestMode, Strategy
from backend.services.backtest_engine.data_manager import BacktestDataManager
from backend.services.backtest_engine.cost_model import CostModel
# pipeline_replay 只有函数，没有类
from backend.services.backtest_engine.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardConfig,
    WalkForwardResult,
)
# 整改#1/#14：过拟合诊断、损失注册表、前视检测
from backend.services.backtest_engine.overfitting_metrics import (
    compute_pbo_cscv,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    min_backtest_length,
    CSCVResult,
)
from backend.services.backtest_engine.loss_functions import LOSS_REGISTRY, get_loss, score
from backend.services.backtest_engine.lookahead_analysis import (
    LookaheadAnalyzer,
    LookaheadReport,
    run_lookahead_check,
)

__all__ = [
    'BacktestEngine',
    'BacktestConfig',
    'BacktestMode',
    'Strategy',
    'BacktestDataManager',
    'CostModel',
    'WalkForwardAnalyzer',
    'WalkForwardConfig',
    'WalkForwardResult',
    # 整改#1 过拟合诊断
    'compute_pbo_cscv',
    'deflated_sharpe_ratio',
    'probabilistic_sharpe_ratio',
    'min_backtest_length',
    'CSCVResult',
    'LOSS_REGISTRY',
    'get_loss',
    'score',
    # 整改#14 前视检测
    'LookaheadAnalyzer',
    'LookaheadReport',
    'run_lookahead_check',
]
