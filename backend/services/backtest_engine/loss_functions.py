"""回测优化的损失/目标函数注册表 —— 对标 Freqtrade hyperopt_loss。

每个损失函数接收一个 BacktestResult，返回一个"越大越好"的标量（最小化类指标
已取负，如 max_drawdown / ulcer）。WalkForward / Optuna 通过 key 选择目标。
"""
from __future__ import annotations

from typing import Callable, Dict


def _get(result, name, default=0.0) -> float:
    try:
        val = getattr(result, name, default)
        return float(val) if val is not None else float(default)
    except Exception:  # noqa: BLE001
        return float(default)


LOSS_REGISTRY: Dict[str, Callable[[object], float]] = {
    "sharpe": lambda r: _get(r, "sharpe_ratio"),
    "sharpe_ratio": lambda r: _get(r, "sharpe_ratio"),
    "sortino": lambda r: _get(r, "sortino_ratio"),
    "sortino_ratio": lambda r: _get(r, "sortino_ratio"),
    "calmar": lambda r: _get(r, "calmar_ratio"),
    "calmar_ratio": lambda r: _get(r, "calmar_ratio"),
    "total_return": lambda r: _get(r, "total_return"),
    "win_rate": lambda r: _get(r, "win_rate"),
    "profit_factor": lambda r: _get(r, "profit_factor"),
    # 最小化类：取负，使"越大越好"统一
    "max_drawdown": lambda r: -abs(_get(r, "max_drawdown")),
    # 复合：单位回撤的夏普
    "sharpe_dd": lambda r: _get(r, "sharpe_ratio") / (abs(_get(r, "max_drawdown")) + 1e-9),
    # ulcer_index 可能不存在于 BacktestResult，_get 兜底 0
    "ulcer": lambda r: -abs(_get(r, "ulcer_index")),
}


def get_loss(name: str) -> Callable[[object], float]:
    """按名取损失函数；未知名回退到 sharpe。"""
    return LOSS_REGISTRY.get((name or "sharpe").strip().lower(), LOSS_REGISTRY["sharpe"])


def score(result, name: str = "sharpe") -> float:
    """便捷：直接对某个 BacktestResult 打分（越大越好）。"""
    return get_loss(name)(result)
