"""
在线因子权重学习（P4.3，方案 §P4.3 / §2.3.6）。

目标：每 bar 流式更新因子→收益线性模型（单样本 learn_one，永不批量重训）。
    平稳段在线模型 vs 离线持平；漂移段适应更快（延迟更低）。

无依赖设计：纯 numpy 在线 SGD 线性回归（River 可选加速，缺失则降级）。
DriftWatcher 的 ONLINE_WEIGHT_RESET 策略调用本模块重置权重。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class OnlineLinearConfig:
    """在线线性模型配置。"""
    learning_rate: float = 0.01
    l2_reg: float = 1e-4   # L2 正则防过拟合
    n_factors: int = 0     # 因子数（首样本时推断）


class OnlineLinearModel:
    """
    在线线性回归（SGD，单样本更新）。

    model: y = w · x + b
    每 bar：w -= lr * (grad + l2 * w)
    """

    def __init__(self, config: OnlineLinearConfig | None = None):
        self.config = config or OnlineLinearConfig()
        self.weights: np.ndarray = np.array([])
        self.bias: float = 0.0
        self._n_samples: int = 0
        self._try_river()

    def _try_river(self) -> None:
        """尝试用 River 的在线线性模型（可选）。"""
        self._river_model = None
        try:
            from river import linear_model as _river_lm
            self._river_model = _river_lm.LinearRegression(
                optimizer=__import__("river").optimizers.SGD(self.config.learning_rate),
                l2=self.config.l2_reg,
            )
        except ImportError:
            pass

    def learn_one(self, x: np.ndarray, y: float) -> None:
        """
        单样本在线更新。

        x: 因子值向量 (n_factors,)
        y: 目标（如 Triple-Barrier 标签或远期收益）
        """
        x = np.asarray(x, dtype=float)
        if len(self.weights) == 0:
            n = len(x) if self.config.n_factors == 0 else self.config.n_factors
            self.weights = np.zeros(n)

        pred = self._predict_raw(x)
        error = pred - y
        # SGD 梯度：d(error^2)/dw = 2*error*x
        grad = error * x
        self.weights -= self.config.learning_rate * (grad + self.config.l2_reg * self.weights)
        self.bias -= self.config.learning_rate * error
        self._n_samples += 1

    def predict_one(self, x: np.ndarray) -> float:
        """单样本预测。"""
        x = np.asarray(x, dtype=float)
        if len(self.weights) == 0:
            return 0.0
        return self._predict_raw(x)

    def _predict_raw(self, x: np.ndarray) -> float:
        return float(np.dot(self.weights[:len(x)], x) + self.bias)

    def reset(self) -> None:
        """重置权重（DriftWatcher 的 ONLINE_WEIGHT_RESET 策略调用）。"""
        if len(self.weights) > 0:
            self.weights = np.zeros_like(self.weights)
        self.bias = 0.0
        self._n_samples = 0

    def weight_norm(self) -> float:
        """权重 L2 范数（监控用）。"""
        return float(np.linalg.norm(self.weights))

    def feature_importance(self, names: list[str] | None = None) -> dict[str, float]:
        """因子重要性（归一化权重绝对值）。

        names 与当前 weights 同序时，键为真实 factor_id；未传则回退 f0/f1（兼容旧测）。
        """
        if len(self.weights) == 0:
            return {}
        abs_w = np.abs(self.weights)
        total = abs_w.sum()
        n = len(self.weights)
        if names is not None and len(names) == n:
            keys = [str(x) for x in names]
        else:
            keys = [f"f{i}" for i in range(n)]
        if total < 1e-12:
            return {keys[i]: 0.0 for i in range(n)}
        return {keys[i]: float(abs_w[i] / total) for i in range(n)}

    def stats(self) -> dict:
        return {
            "n_samples": self._n_samples,
            "n_factors": len(self.weights),
            "weight_norm": self.weight_norm(),
            "has_river": self._river_model is not None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# [2026-09-07] 常驻在线模型（真·在线学习，替代每轮临时新建）
# ═══════════════════════════════════════════════════════════════════════════
#
# 此前 _update_online_weights 每轮进化新建 OnlineLinearModel、每币喂 1 个样本、
# 回写权重后丢弃——这不是在线学习，是「每轮一次性拟合」。这里提供进程级常驻
# 单例：权重持久化到磁盘，跨轮/跨重启累积学习；DriftWatcher 触发时 reset。
#
# 设计：
#   - 单例 get_resident_online_model()，权重落 data/online_linear_resident.npz
#   - 每次 learn_one 后增量落盘（原子写），重启启动时加载
#   - reset() 清权重并落盘（漂移自愈入口，见 drift_watcher ONLINE_WEIGHT_RESET）

import os as _os
import threading as _threading

_RESIDENT_LOCK = _threading.Lock()
_RESIDENT_MODEL: "OnlineLinearModel | None" = None


def _resident_path() -> str:
    return _os.path.join("data", "online_linear_resident.npz")


def get_resident_online_model() -> "OnlineLinearModel":
    """取进程级常驻在线模型（懒加载 + 磁盘恢复）。"""
    global _RESIDENT_MODEL
    with _RESIDENT_LOCK:
        if _RESIDENT_MODEL is None:
            m = OnlineLinearModel()
            try:
                p = _resident_path()
                if _os.path.exists(p):
                    d = np.load(p, allow_pickle=False)
                    m.weights = d["weights"]
                    m.bias = float(d["bias"])
                    m._n_samples = int(d["n_samples"])
            except Exception:
                pass
            _RESIDENT_MODEL = m
        return _RESIDENT_MODEL


def resident_learn_one(x: np.ndarray, y: float, *, persist: bool = True) -> None:
    """常驻模型单样本学习 + 增量落盘。"""
    m = get_resident_online_model()
    m.learn_one(x, y)
    if persist:
        save_resident_online_model()


def save_resident_online_model() -> None:
    """原子落盘常驻模型权重。"""
    m = get_resident_online_model()
    try:
        p = _resident_path()
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        np.savez(tmp, weights=m.weights, bias=m.bias, n_samples=m._n_samples)
        # np.savez 会自动加 .npz；规范到目标名
        if _os.path.exists(tmp + ".npz"):
            _os.replace(tmp + ".npz", p)
        elif _os.path.exists(tmp):
            _os.replace(tmp, p)
    except Exception:
        pass


def reset_resident_online_model(reason: str = "") -> None:
    """漂移自愈：重置常驻模型权重并落盘。DriftWatcher ONLINE_WEIGHT_RESET 调用。"""
    m = get_resident_online_model()
    m.reset()
    save_resident_online_model()
    import logging as _lg
    _lg.getLogger(__name__).warning("[OnlineWeights] 常驻模型已重置（漂移自愈）: %s", reason)
