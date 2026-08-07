"""LinearQPolicy — 轻量强化学习策略（numpy 线性 Q 函数）

选择线性 Q 而非深度网络的原因：
  - 依赖最小（只需 numpy），训练快、可解释、易持久化为 JSON，适合先影子上线；
  - 交易特征已由因子工程高度加工，线性 Q 足以作为 RL 决策 baseline；
  - 后续需要时可无缝替换为 torch DQN（接口保持一致）。

支持：从 ReplayBuffer 离线训练 + 在线单步增量更新 + 特征动态扩展 + 保存/加载。
动作：0 hold / 1 open_long / 2 open_short / 3 close。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import numpy as np

from .env import N_ACTIONS, ACTION_NAMES

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


POLICY_PATH = os.path.join(_repo_root(), "data", "rl_policy.json")

_lock = threading.Lock()


class LinearQPolicy:
    """线性 Q 策略（单例 policy）。Q(s,a) = w_a · [features, bias]。"""

    def __init__(self, lr: float = 0.05, gamma: float = 0.95) -> None:
        self.lr = lr
        self.gamma = gamma
        self.feature_names: List[str] = []      # 有序特征名
        self.weights: np.ndarray = np.zeros((N_ACTIONS, 1))  # [n_actions, n_feat+1(bias)]
        self._trained_steps = 0
        self._loaded = False

    # ── 特征处理 ──

    def _ensure_features(self, state: Dict[str, Any]) -> None:
        """遇到新特征名时动态扩展权重矩阵（末列恒为 bias）。"""
        new = [k for k in state.keys() if k not in self.feature_names]
        if not new and self.weights.shape[1] == len(self.feature_names) + 1:
            return
        for k in new:
            self.feature_names.append(k)
        n_feat = len(self.feature_names)
        w = np.zeros((N_ACTIONS, n_feat + 1))
        # 迁移旧权重（特征列 + bias 列）
        old = self.weights
        if old.shape[1] >= 1:
            old_feat = old.shape[1] - 1
            w[:, :old_feat] = old[:, :old_feat]
            w[:, -1] = old[:, -1]  # bias
        self.weights = w

    def featurize(self, state: Dict[str, Any]) -> np.ndarray:
        self._ensure_features(state)
        vec = np.zeros(len(self.feature_names) + 1)
        for i, name in enumerate(self.feature_names):
            try:
                v = float(state.get(name, 0.0))
                if np.isnan(v) or np.isinf(v):
                    v = 0.0
                vec[i] = v
            except (TypeError, ValueError):
                vec[i] = 0.0
        vec[-1] = 1.0  # bias
        return vec

    # ── 推理 ──

    def q_values(self, state: Dict[str, Any]) -> np.ndarray:
        x = self.featurize(state)
        return self.weights @ x

    def act(self, state: Dict[str, Any], epsilon: float = 0.0) -> int:
        if epsilon > 0 and np.random.random() < epsilon:
            return int(np.random.randint(N_ACTIONS))
        q = self.q_values(state)
        return int(np.argmax(q))

    def act_detail(self, state: Dict[str, Any]) -> Dict[str, Any]:
        q = self.q_values(state)
        a = int(np.argmax(q))
        # softmax 置信度
        z = q - np.max(q)
        e = np.exp(z)
        probs = e / (np.sum(e) + 1e-12)
        return {
            "action": a,
            "action_name": ACTION_NAMES.get(a, str(a)),
            "q_values": [round(float(v), 5) for v in q],
            "confidence": round(float(probs[a]), 4),
        }

    # ── 更新 / 训练 ──

    def update(self, state: Dict[str, Any], action: int, reward: float,
               next_state: Optional[Dict[str, Any]] = None, done: bool = True) -> float:
        """单步 Q-learning 更新，返回 TD 误差。"""
        with _lock:
            x = self.featurize(state)
            q_sa = float(self.weights[action] @ x)
            target = float(reward)
            if not done and next_state is not None:
                q_next = self.q_values(next_state)
                target += self.gamma * float(np.max(q_next))
            td = target - q_sa
            self.weights[action] += self.lr * td * x
            self._trained_steps += 1
            return td

    def train_from_replay(self, batch_size: int = 256, epochs: int = 5) -> Dict[str, Any]:
        """从 ReplayBuffer 离线训练。"""
        from .replay_buffer import replay_buffer

        total = replay_buffer.count()
        if total == 0:
            return {"ok": False, "error": "replay buffer 为空", "trained_steps": self._trained_steps}

        td_errors: List[float] = []
        for _ in range(max(1, epochs)):
            batch = replay_buffer.sample(batch_size)
            for tr in batch:
                state = tr.get("state") or {}
                if not isinstance(state, dict):
                    continue
                action = int(tr.get("action", 0)) % N_ACTIONS
                reward = float(tr.get("reward", 0.0))
                nxt = tr.get("next_state") if isinstance(tr.get("next_state"), dict) else None
                done = bool(tr.get("done", True))
                td_errors.append(abs(self.update(state, action, reward, nxt, done)))

        self.save()
        return {
            "ok": True,
            "trained_steps": self._trained_steps,
            "samples_seen": len(td_errors),
            "mean_abs_td": round(float(np.mean(td_errors)), 6) if td_errors else 0.0,
            "n_features": len(self.feature_names),
        }

    # ── 持久化 ──

    def save(self, path: str = POLICY_PATH) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "lr": self.lr,
                    "gamma": self.gamma,
                    "feature_names": self.feature_names,
                    "weights": self.weights.tolist(),
                    "trained_steps": self._trained_steps,
                }, f)
        except Exception as exc:
            logger.debug("[LinearQPolicy] save 失败: %s", exc)

    def load(self, path: str = POLICY_PATH) -> bool:
        if self._loaded:
            return True
        try:
            if not os.path.isfile(path):
                self._loaded = True
                return False
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.lr = d.get("lr", self.lr)
            self.gamma = d.get("gamma", self.gamma)
            self.feature_names = d.get("feature_names", [])
            w = d.get("weights")
            if w:
                self.weights = np.array(w)
            self._trained_steps = d.get("trained_steps", 0)
            self._loaded = True
            return True
        except Exception as exc:
            logger.debug("[LinearQPolicy] load 失败: %s", exc)
            self._loaded = True
            return False

    def stats(self) -> Dict[str, Any]:
        return {
            "trained_steps": self._trained_steps,
            "n_features": len(self.feature_names),
            "n_actions": N_ACTIONS,
            "lr": self.lr,
            "gamma": self.gamma,
        }


# 单例
policy = LinearQPolicy()
policy.load()
