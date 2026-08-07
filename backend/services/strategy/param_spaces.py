"""
声明式参数空间（整改#6）—— 对标 Freqtrade IntParameter / Jesse hp.*。

策略类以类属性形式自声明可优化参数范围，替代 walk_forward.py 里手写的 param_grid dict。
提供反射收集 + 转 grid / optuna / 随机采样，供 WFO（整改#1）直接消费。

说明：Parameter 用 per-instance 存储（obj.__dict__），避免"类级描述符共享单一 value"的
经典 bug；未绑定实例时（直接访问类属性）返回描述符自身，便于 collect_parameters 反射。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional


class Parameter:
    """基类：策略类属性，自声明可优化范围。"""

    def __init__(self, low, high, default, space: str = "buy", optimize: bool = True):
        self.low = low
        self.high = high
        self.default = default
        self.space = space            # 'buy'|'sell'|'roi'|'stoploss'|'protection'
        self.optimize = optimize
        self._name: Optional[str] = None

    def __set_name__(self, owner, name):
        self._name = name

    def _store_key(self) -> str:
        return f"__param_{self._name or id(self)}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self                 # 类级访问 → 返回描述符本身（供反射）
        return obj.__dict__.get(self._store_key(), self.default)

    def __set__(self, obj, value):
        obj.__dict__[self._store_key()] = value

    def sample(self) -> Any:
        raise NotImplementedError

    def to_optuna(self, trial, name: str):
        raise NotImplementedError

    def grid_values(self, n_points: int = 3) -> List[Any]:
        raise NotImplementedError


class IntParameter(Parameter):
    def sample(self) -> int:
        return random.randint(int(self.low), int(self.high))

    def to_optuna(self, trial, name):
        return trial.suggest_int(name, int(self.low), int(self.high))

    def grid_values(self, n_points: int = 3) -> List[int]:
        lo, hi = int(self.low), int(self.high)
        if hi <= lo:
            return [lo]
        n = min(n_points, hi - lo + 1)
        step = (hi - lo) / (n - 1) if n > 1 else 0
        vals = sorted({int(round(lo + i * step)) for i in range(n)})
        return vals


class FloatParameter(Parameter):
    def sample(self) -> float:
        return random.uniform(float(self.low), float(self.high))

    def to_optuna(self, trial, name):
        return trial.suggest_float(name, float(self.low), float(self.high))

    def grid_values(self, n_points: int = 3) -> List[float]:
        lo, hi = float(self.low), float(self.high)
        if hi <= lo or n_points <= 1:
            return [lo]
        step = (hi - lo) / (n_points - 1)
        return [round(lo + i * step, 10) for i in range(n_points)]


class CategoricalParameter(Parameter):
    def __init__(self, categories: List, default, space: str = "buy", optimize: bool = True):
        super().__init__(0, max(0, len(categories) - 1), default, space, optimize)
        self.categories = list(categories)

    def sample(self):
        return random.choice(self.categories)

    def to_optuna(self, trial, name):
        return trial.suggest_categorical(name, self.categories)

    def grid_values(self, n_points: int = 3) -> List[Any]:
        return list(self.categories)


def collect_parameters(strategy_class) -> Dict[str, Parameter]:
    """反射收集策略类上所有 Parameter 描述符（含继承链）。"""
    params: Dict[str, Parameter] = {}
    for klass in reversed(getattr(strategy_class, "__mro__", [strategy_class])):
        for name, attr in vars(klass).items():
            if isinstance(attr, Parameter):
                params[name] = attr
    return params


def to_defaults(params: Dict[str, Parameter]) -> Dict[str, Any]:
    return {name: p.default for name, p in params.items()}


def to_grid(params: Dict[str, Parameter], n_points: int = 3,
            only_optimize: bool = True) -> Dict[str, List[Any]]:
    """转 WFO 网格：{name: [候选值,...]}（只含 optimize=True 的参数）。"""
    grid: Dict[str, List[Any]] = {}
    for name, p in params.items():
        if only_optimize and not p.optimize:
            continue
        grid[name] = p.grid_values(n_points)
    return grid


def sample_random(params: Dict[str, Parameter], only_optimize: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, p in params.items():
        out[name] = p.sample() if (p.optimize or not only_optimize) else p.default
    return out


def suggest_with_optuna(params: Dict[str, Parameter], trial,
                        only_optimize: bool = True) -> Dict[str, Any]:
    """在 optuna trial 上按原生类型建议参数（int/float/categorical）。"""
    out: Dict[str, Any] = {}
    for name, p in params.items():
        if only_optimize and not p.optimize:
            out[name] = p.default
            continue
        out[name] = p.to_optuna(trial, name)
    return out


def build_param_grid_from_strategy(strategy_class, n_points: int = 3) -> Dict[str, List[Any]]:
    """便捷：策略类 → WFO 可直接用的 param_grid。"""
    return to_grid(collect_parameters(strategy_class), n_points=n_points)
