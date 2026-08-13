"""
统一监督学习模型接口 —— 对标 FreqAI IFreqaiModel / Qlib Model。

所有模型共享 fit/predict/save/load，一行配置切换后端。
重后端（lightgbm/catboost/torch）惰性导入；get_model 在后端缺失时自动降级到
LinearTrendModel（sklearn Ridge，纯轻依赖），保证管线永远可跑。
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import List, Optional

import numpy as np
import pandas as pd

from backend.services.ml.training_context import TrainingContext

logger = logging.getLogger(__name__)


class SupervisedModel(ABC):
    """统一监督学习模型接口。"""

    @abstractmethod
    def fit(self, ctx: TrainingContext) -> None: ...

    @abstractmethod
    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series: ...

    @abstractmethod
    def save(self, path: str) -> None: ...

    @abstractmethod
    def load(self, path: str) -> None: ...

    @property
    @abstractmethod
    def model_type(self) -> str: ...

    # 公共工具
    @staticmethod
    def _clean_X(features: pd.DataFrame, feature_columns: List[str]) -> pd.DataFrame:
        cols = [c for c in feature_columns if c in features.columns]
        X = features[cols].replace([np.inf, -np.inf], np.nan)
        return X.fillna(0.0)


class LinearTrendModel(SupervisedModel):
    """Ridge 线性回归 —— 零重依赖兜底模型（sklearn，必装）。"""

    def __init__(self, alpha: float = 1.0):
        self._alpha = alpha
        self._model = None
        self._columns: List[str] = []

    def fit(self, ctx: TrainingContext) -> None:
        from sklearn.linear_model import Ridge

        X, y = ctx.aligned_train_xy()
        self._columns = list(X.columns)
        if len(X) < 2:
            self._model = None
            return
        self._model = Ridge(alpha=self._alpha)
        self._model.fit(X.values, y.values)

    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        cols = self._columns or feature_columns
        X = self._clean_X(features, cols)
        if self._model is None:
            return pd.Series(0.0, index=features.index)
        preds = self._model.predict(X.values)
        return pd.Series(preds, index=features.index)

    def save(self, path: str) -> None:
        import joblib

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({"model": self._model, "columns": self._columns, "alpha": self._alpha}, path)

    def load(self, path: str) -> None:
        import joblib

        data = joblib.load(path)
        self._model = data["model"]
        self._columns = data["columns"]
        self._alpha = data.get("alpha", 1.0)

    @property
    def model_type(self) -> str:
        return "linear"


class LightGBMTrendModel(SupervisedModel):
    """LightGBM 学习因子非线性加权 —— 对标 FreqAI LightGBMRegressor。"""

    def __init__(self, **params):
        self._params = params or {"n_estimators": 200, "learning_rate": 0.05, "num_leaves": 31,
                                  "min_child_samples": 20, "verbose": -1}
        self._model = None
        self._columns: List[str] = []

    def fit(self, ctx: TrainingContext) -> None:
        import lightgbm as lgb

        X, y = ctx.aligned_train_xy()
        self._columns = list(X.columns)
        if len(X) < 5:
            self._model = None
            return
        self._model = lgb.LGBMRegressor(**self._params)
        sw = ctx.aligned_train_weights()
        if sw is not None and len(sw) == len(X):
            self._model.fit(X.values, y.values, sample_weight=sw.values)
        else:
            self._model.fit(X.values, y.values)

    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        cols = self._columns or feature_columns
        X = self._clean_X(features, cols)
        if self._model is None:
            return pd.Series(0.0, index=features.index)
        preds = self._model.predict(X.values)
        return pd.Series(preds, index=features.index)

    def save(self, path: str) -> None:
        import joblib

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({"model": self._model, "columns": self._columns, "params": self._params}, path)

    def load(self, path: str) -> None:
        import joblib

        data = joblib.load(path)
        self._model = data["model"]
        self._columns = data["columns"]
        self._params = data.get("params", self._params)

    @property
    def model_type(self) -> str:
        return "lightgbm"


class CatBoostModel(SupervisedModel):
    """CatBoost —— 对标 FreqAI CatboostRegressor（catboost 缺失时抛错，由 get_model 降级）。"""

    def __init__(self, **params):
        self._params = params or {"iterations": 300, "learning_rate": 0.05, "depth": 6, "verbose": False}
        self._model = None
        self._columns: List[str] = []

    def fit(self, ctx: TrainingContext) -> None:
        from catboost import CatBoostRegressor

        X, y = ctx.aligned_train_xy()
        self._columns = list(X.columns)
        if len(X) < 5:
            self._model = None
            return
        self._model = CatBoostRegressor(**self._params)
        self._model.fit(X.values, y.values)

    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        cols = self._columns or feature_columns
        X = self._clean_X(features, cols)
        if self._model is None:
            return pd.Series(0.0, index=features.index)
        return pd.Series(self._model.predict(X.values), index=features.index)

    def save(self, path: str) -> None:
        import joblib

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({"model": self._model, "columns": self._columns, "params": self._params}, path)

    def load(self, path: str) -> None:
        import joblib

        data = joblib.load(path)
        self._model = data["model"]
        self._columns = data["columns"]
        self._params = data.get("params", self._params)

    @property
    def model_type(self) -> str:
        return "catboost"


class PyTorchGRUModel(SupervisedModel):
    """GRU 时序回归 —— 对标 FreqAI PyTorchTransformerRegressor / Qlib GRU。

    以长度 seq_len 的滑动窗口把因子矩阵转成序列，预测序列末端的前瞻收益。
    - device：训练读 ML_TRAIN_DEVICE / 推理读 ML_INFER_DEVICE（auto = cuda
      可用且显存充足才用，否则 cpu；单卡 1070 阶段即走此策略）
    - 训练：时序 train/val 切分 + early stopping + minibatch；CUDA OOM 自动降级 CPU 重训
    """

    def __init__(
        self,
        seq_len: int = 8,
        hidden_size: int = 32,
        epochs: int = 20,
        lr: float = 1e-3,
        num_layers: int = 1,
        dropout: float = 0.0,
        batch_size: int = 64,
        patience: int = 5,
        device: Optional[str] = None,
    ):
        self.seq_len = seq_len
        self.hidden_size = hidden_size
        self.epochs = epochs
        self.lr = lr
        self.num_layers = num_layers
        self.dropout = dropout
        self.batch_size = batch_size
        self.patience = patience
        self._device = device
        self._model = None
        self._columns: List[str] = []
        self._mu = None
        self._sd = None

    # ---------- device 解析（单卡 1070 阶段：显存充足才上卡） ----------

    @staticmethod
    def _cuda_usable(min_free_gb: float = 2.0) -> bool:
        try:
            import torch
            if not torch.cuda.is_available():
                return False
            free_mb, _total = torch.cuda.mem_get_info(0)
            return free_mb >= min_free_gb * 1024 * 1024 * 1024
        except Exception:
            return False

    def _resolve_device(self, infer: bool = False):
        """按 env（ML_TRAIN_DEVICE / ML_INFER_DEVICE）解析 torch.device。"""
        import torch

        env_key = "ML_INFER_DEVICE" if infer else "ML_TRAIN_DEVICE"
        val = (self._device or os.environ.get(env_key) or "auto").strip().lower() or "auto"
        if val.startswith("cuda"):
            if torch.cuda.is_available():
                return torch.device(val)
            return torch.device("cpu")
        if val == "auto":
            return torch.device("cuda") if self._cuda_usable() else torch.device("cpu")
        return torch.device("cpu")

    def _build_sequences(self, X: np.ndarray, y: Optional[np.ndarray] = None):
        seqs, targets = [], []
        for i in range(self.seq_len, len(X) + 1):
            seqs.append(X[i - self.seq_len:i])
            if y is not None:
                targets.append(y[i - 1])
        import numpy as _np
        xs = _np.asarray(seqs, dtype="float32") if seqs else _np.empty((0, self.seq_len, X.shape[1]), dtype="float32")
        ts = _np.asarray(targets, dtype="float32") if targets else None
        return xs, ts

    def _build_net(self, in_dim: int):
        import torch.nn as nn

        class _GRU(nn.Module):
            def __init__(self, in_dim, hidden, num_layers, dropout):
                super().__init__()
                # PyTorch 要求 num_layers>1 时 dropout 才生效
                self.gru = nn.GRU(
                    in_dim, hidden, num_layers=num_layers,
                    dropout=dropout if num_layers > 1 else 0.0,
                    batch_first=True,
                )
                self.head = nn.Linear(hidden, 1)

            def forward(self, x):
                out, _ = self.gru(x)
                return self.head(out[:, -1, :]).squeeze(-1)

        return _GRU(in_dim, self.hidden_size, self.num_layers, self.dropout)

    def _fit_on_device(self, model, train_x, train_y, val_x, val_y, device):
        """minibatch 训练 + 时序验证集 early stopping（返回 best 权重）。"""
        import torch
        import torch.nn as nn

        model.to(device)
        opt = torch.optim.Adam(model.parameters(), lr=self.lr)
        loss_fn = nn.MSELoss()
        xt = torch.from_numpy(train_x).to(device)
        yt = torch.from_numpy(train_y).to(device)
        xv = torch.from_numpy(val_x).to(device)
        yv = torch.from_numpy(val_y).to(device)
        n = len(xt)
        batch = max(1, min(self.batch_size, n))
        rng = np.random.default_rng(42)
        best_val = float("inf")
        best_state = None
        bad_epochs = 0
        model.train()
        for _ in range(self.epochs):
            order = rng.permutation(n)
            for i in range(0, n, batch):
                idx = order[i:i + batch]
                opt.zero_grad()
                pred = model(xt[idx])
                loss = loss_fn(pred, yt[idx])
                loss.backward()
                opt.step()
            model.eval()
            with torch.no_grad():
                val_loss = float(loss_fn(model(xv), yv).item())
            model.train()
            if val_loss < best_val - 1e-6:
                best_val = val_loss
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                bad_epochs = 0
            else:
                bad_epochs += 1
                if bad_epochs >= self.patience:
                    break
        if best_state is not None:
            model.load_state_dict(best_state)
        model.eval()

    def fit(self, ctx: TrainingContext) -> None:
        import torch

        X, y = ctx.aligned_train_xy()
        self._columns = list(X.columns)
        Xv = X.values.astype("float32")
        yv = y.values.astype("float32")
        if len(Xv) <= self.seq_len:
            self._model = None
            return
        # 标准化（仅用训练集统计，防前视）
        self._mu = Xv.mean(axis=0)
        self._sd = Xv.std(axis=0) + 1e-8
        Xn = (Xv - self._mu) / self._sd

        xs, ts = self._build_sequences(Xn, yv)
        if len(xs) == 0:
            self._model = None
            return
        n_features = Xn.shape[1]

        # 时序切分：后 20% 为验证集（early stopping 用，不参与梯度）
        split = max(int(len(xs) * 0.8), 1)
        train_x, val_x = xs[:split], xs[split:]
        train_y, val_y = ts[:split], ts[split:]

        torch.manual_seed(42)
        device = self._resolve_device()
        model = self._build_net(n_features)
        try:
            self._fit_on_device(model, train_x, train_y, val_x, val_y, device)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() and device.type == "cuda":
                # 单卡 1070 显存预算内失败 → 降级 CPU 重训，保底完成
                logger.warning("[ML] PyTorchGRU CUDA OOM，降级 CPU 重训: %s", e)
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                model = self._build_net(n_features)
                self._fit_on_device(model, train_x, train_y, val_x, val_y, torch.device("cpu"))
            else:
                raise
        self._model = model

    def predict(self, features: pd.DataFrame, feature_columns: List[str]) -> pd.Series:
        import torch

        cols = self._columns or feature_columns
        X = self._clean_X(features, cols)
        if self._model is None or len(X) < self.seq_len:
            return pd.Series(0.0, index=features.index)
        Xv = X.values.astype("float32")
        Xn = (Xv - self._mu) / self._sd
        xs, _ = self._build_sequences(Xn, None)
        device = self._resolve_device(infer=True)
        self._model.to(device)
        try:
            with torch.no_grad():
                preds = self._model(torch.from_numpy(xs).to(device)).cpu().numpy()
        finally:
            # 推理后回 CPU，避免常驻显存占用（单卡阶段不抢占训练预算）
            self._model.to("cpu")
        # 前 seq_len-1 行无完整序列 → 填 0
        out = np.concatenate([np.zeros(self.seq_len - 1, dtype="float32"), preds])
        return pd.Series(out[: len(X)], index=features.index)

    def save(self, path: str) -> None:
        import torch

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save({
            "state_dict": self._model.state_dict() if self._model is not None else None,
            "columns": self._columns,
            "mu": self._mu, "sd": self._sd,
            "hparams": {"seq_len": self.seq_len, "hidden_size": self.hidden_size,
                        "epochs": self.epochs, "lr": self.lr,
                        "num_layers": self.num_layers, "dropout": self.dropout,
                        "batch_size": self.batch_size, "patience": self.patience},
        }, path)

    def load(self, path: str) -> None:
        import torch

        data = torch.load(path, weights_only=False)
        self._columns = data["columns"]
        self._mu = data["mu"]
        self._sd = data["sd"]
        hp = data.get("hparams", {})
        self.seq_len = hp.get("seq_len", self.seq_len)
        self.hidden_size = hp.get("hidden_size", self.hidden_size)
        self.num_layers = hp.get("num_layers", 1)
        self.dropout = hp.get("dropout", 0.0)
        if data["state_dict"] is not None:
            n_features = len(self._columns)
            self._model = self._build_net(n_features)
            self._model.load_state_dict(data["state_dict"])
            self._model.eval()

    @property
    def model_type(self) -> str:
        return "pytorch_gru"


MODEL_REGISTRY = {
    "linear": LinearTrendModel,
    "lightgbm": LightGBMTrendModel,
    "catboost": CatBoostModel,
    "pytorch_gru": PyTorchGRUModel,
}

# 各后端所需的可选依赖模块名（用于 get_model 的可用性探测）
_BACKEND_REQUIRES = {
    "lightgbm": "lightgbm",
    "catboost": "catboost",
    "pytorch_gru": "torch",
}


def _backend_available(model_type: str) -> bool:
    mod = _BACKEND_REQUIRES.get(model_type)
    if not mod:
        return True  # linear 仅需 sklearn（必装）
    import importlib.util

    return importlib.util.find_spec(mod) is not None


def get_model(model_type: str, **kwargs) -> SupervisedModel:
    """按类型取模型；后端不可用时自动降级到 linear（零风险）。"""
    mt = (model_type or "lightgbm").strip().lower()
    if mt not in MODEL_REGISTRY:
        logger.warning("[ML] 未知模型类型 %s，降级 linear", mt)
        return LinearTrendModel()
    if not _backend_available(mt):
        logger.warning("[ML] 模型 %s 的后端依赖缺失，降级 linear", mt)
        return LinearTrendModel()
    return MODEL_REGISTRY[mt](**kwargs)
