"""
RLPolicyOptimizer — 强化学习策略优化器

与现有系统并行运行，输出作为"建议"而非"指令"。
采用 try/except 降级策略，stable-baselines3 未安装时自动禁用。
设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第4.5.3节

v4（P0-4 / P1-2 闭环整改）:
- 增加 save/load 持久化，默认路径 models/drl/ppo_latest.zip
- 增加 model_version（训练完成时间戳），写回 SystemCoordinatorState.drl_model_version
- load 时校验 feature_dim（观测维度），不匹配时拒绝加载并回退随机策略
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 默认模型持久化路径（项目根下 models/drl/ppo_latest.zip）
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "drl"
_DEFAULT_MODEL_PATH = _DEFAULT_MODEL_DIR / "ppo_latest.zip"


class RLPolicyOptimizer:
    """
    强化学习策略优化器

    使用 PPO 算法训练交易策略。
    以"影子模式"运行，输出作为建议而非指令。

    当 stable-baselines3 未安装时，自动降级为不可用状态。
    """

    # 当前 TradingEnv observation 维度（与 _build_observation/returns 一致）
    # 若未来 env schema 变更，必须同步改这里以让 load 时的版本校验生效
    EXPECTED_FEATURE_DIM = 10

    def __init__(self):
        self.model = None
        self._ppo_class = None
        self._is_available = False
        self._total_timesteps_trained = 0
        self._prediction_count = 0
        self._model_version: str = "v0"
        self._model_feature_dim: Optional[int] = None

        try:
            from stable_baselines3 import PPO
            self._ppo_class = PPO
            self._is_available = True
        except ImportError:
            logger.warning(
                "[RLPolicyOptimizer] stable-baselines3 not installed, DRL disabled"
            )

    @property
    def is_available(self) -> bool:
        """DRL 是否可用（stable-baselines3 已装）"""
        return self._is_available

    @property
    def model_version(self) -> str:
        """当前模型版本号（训练完成时间戳字符串，未训练为 v0）"""
        return self._model_version

    def train(
        self,
        env,
        total_timesteps: int = 100000,
        learning_rate: float = 3e-4,
        save_after: bool = True,
        progress_callback: callable = None,
    ) -> bool:
        """
        训练 PPO 模型

        Args:
            env: TradingEnv 实例（需兼容 gymnasium 接口）
            total_timesteps: 总训练步数
            learning_rate: 学习率
            save_after: 训练成功后是否自动 save 到默认路径（ppo_latest.zip）
            progress_callback: 可选，训练进度回调 callback(pct: float, current_ts: int, total_ts: int)

        Returns:
            是否训练成功
        """
        if not self._is_available or self._ppo_class is None:
            logger.warning("[RLPolicyOptimizer] Cannot train: DRL not available")
            return False

        try:
            self.model = self._ppo_class(
                "MlpPolicy",
                env,
                learning_rate=learning_rate,
                n_steps=2048,
                batch_size=64,
                n_epochs=10,
                verbose=0,
            )

            # ── 构建 SB3 进度回调 ──
            def _sb3_callback(_locals: dict, _globals: dict) -> bool:
                if progress_callback:
                    current = _locals.get("num_timesteps", 0)
                    pct = min(99, round(current / total_timesteps * 100, 1))
                    progress_callback(pct, current, total_timesteps)
                return True

            self.model.learn(
                total_timesteps=total_timesteps,
                callback=_sb3_callback if progress_callback else None,
            )
            self._total_timesteps_trained += total_timesteps
            # 版本号 = 训练结束时间戳
            self._model_version = f"ppo_{int(time.time())}"
            # 记录 feature_dim，供 load 时比对
            try:
                self._model_feature_dim = int(env.observation_space.shape[0])
            except Exception:
                self._model_feature_dim = self.EXPECTED_FEATURE_DIM

            if save_after:
                try:
                    self.save()
                except Exception as save_err:
                    logger.warning(f"[RLPolicyOptimizer] save after train failed: {save_err}")
            return True
        except Exception as e:
            logger.error("[RLPolicyOptimizer] Training failed: %s", e)
            return False

    def save(self, path: Optional[str] = None) -> bool:
        """将当前 PPO 模型保存到磁盘。

        Args:
            path: 模型保存路径，默认 models/drl/ppo_latest.zip

        Returns:
            是否保存成功
        """
        if not self._is_available or self.model is None:
            return False
        target = Path(path) if path else _DEFAULT_MODEL_PATH
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # stable-baselines3 save 自动写入 .zip
            self.model.save(str(target))
            logger.info(
                f"[RLPolicyOptimizer] model saved: {target} version={self._model_version}"
            )
            return True
        except Exception as e:
            logger.warning(f"[RLPolicyOptimizer] save failed: {e}")
            return False

    def load(self, path: Optional[str] = None) -> bool:
        """从磁盘加载已保存的 PPO 模型。

        feature_dim 校验：若保存的观测维度与当前 EXPECTED_FEATURE_DIM 不符，
        拒绝加载，回退到随机 / hold 策略，避免 env schema 迁移后错误推断。

        Returns:
            True 加载成功；False 加载失败或校验不通过
        """
        if not self._is_available or self._ppo_class is None:
            return False
        target = Path(path) if path else _DEFAULT_MODEL_PATH
        if not target.exists():
            logger.info(f"[RLPolicyOptimizer] no saved model at {target}")
            return False
        try:
            model = self._ppo_class.load(str(target))
            # feature_dim 校验
            try:
                loaded_dim = int(model.observation_space.shape[0])
            except Exception:
                loaded_dim = None

            if loaded_dim is not None and loaded_dim != self.EXPECTED_FEATURE_DIM:
                logger.warning(
                    f"[RLPolicyOptimizer] loaded model feature_dim={loaded_dim} "
                    f"!= expected={self.EXPECTED_FEATURE_DIM}, refusing to load"
                )
                return False

            self.model = model
            self._model_feature_dim = loaded_dim or self.EXPECTED_FEATURE_DIM
            # 从文件 mtime 推断版本号（训练时已 save 过的话为 ppo_{ts}，这里用 mtime 兜底）
            try:
                mtime_ts = int(target.stat().st_mtime)
                self._model_version = f"ppo_{mtime_ts}"
            except Exception:
                self._model_version = "ppo_loaded"
            logger.info(
                f"[RLPolicyOptimizer] model loaded: {target} feature_dim={loaded_dim}"
            )
            return True
        except Exception as e:
            logger.warning(f"[RLPolicyOptimizer] load failed: {e}")
            return False

    def predict(self, observation: np.ndarray) -> Tuple[float, float]:
        """
        预测动作（方向+仓位大小）

        Args:
            observation: 环境观察值

        Returns:
            (direction, size): 方向 (-1~1) 和仓位大小 (0~1)
        """
        if not self._is_available or self.model is None:
            return 0.0, 0.0  # 无操作

        try:
            action, _ = self.model.predict(observation, deterministic=True)
            self._prediction_count += 1
            return float(action[0]), float(action[1])
        except Exception as e:
            logger.warning("[RLPolicyOptimizer] Prediction failed: %s", e)
            return 0.0, 0.0

    def get_shadow_advice(
        self, observation: np.ndarray
    ) -> dict:
        """
        获取影子模式建议

        Returns:
            dict with 'direction', 'size', 'action' (hold/long/short/close)
        """
        direction, size = self.predict(observation)

        if abs(direction) < 0.2 or size < 0.1:
            action = "hold"
        elif direction > 0:
            action = "long"
        else:
            action = "short"

        if size < 0.05:
            action = "hold"

        return {
            'direction': direction,
            'size': size,
            'action': action,
            'source': 'drl_shadow',
            'confidence': min(abs(direction) * size, 1.0),
        }

    @property
    def stats(self) -> dict:
        """获取优化器统计信息"""
        return {
            'is_available': self._is_available,
            'has_model': self.model is not None,
            'total_timesteps_trained': self._total_timesteps_trained,
            'prediction_count': self._prediction_count,
            'model_version': self._model_version,
            'feature_dim': self._model_feature_dim,
        }
