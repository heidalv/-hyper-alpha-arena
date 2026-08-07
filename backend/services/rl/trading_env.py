"""
TradingEnv — 强化学习交易环境

兼容 Gymnasium 接口的交易环境。
观察空间：因子值 + 账户状态 + 持仓信息
动作空间：[direction(-1~1), size(0~1)]
奖励：风险调整后收益（Sharpe-like）

当 gymnasium 未安装时，提供独立的 TradingEnv 基类。
设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第4.5.2节
"""

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import gymnasium as gym
    from gymnasium import spaces
    HAS_GYM = True
except ImportError:
    HAS_GYM = False
    gym = None
    spaces = None


# 如果 gymnasium 可用，定义一个包装基类
if HAS_GYM:
    class _TradingEnvBase(gym.Env):
        """Gymnasium 兼容基类"""
        metadata = {'render_modes': []}
        
        def __init__(self, *args, **kwargs):
            super().__init__()
            
        def _get_obs(self):
            raise NotImplementedError
            
        def reset(self, seed=None, options=None):
            super().reset(seed=seed)
            return self._get_obs(), {}
            
        def step(self, action):
            raise NotImplementedError


class TradingEnv(_TradingEnvBase if HAS_GYM else object):
    """
    交易环境 — 兼容Gymnasium接口

    当 gymnasium 可用时继承 gym.Env，否则使用独立基类。
    观察空间：因子值(n) + [balance_ratio, position, unrealized_pnl, leverage]
    动作空间：[direction(-1~1), size(0~1)]
    奖励：风险调整后收益（Sharpe-like）
    """

    metadata = {'render_modes': []}

    def __init__(
        self,
        klines: pd.DataFrame,
        factor_outputs: Optional[Dict[str, pd.Series]] = None,
        initial_balance: float = 10000,
        max_leverage: int = 5,
    ):
        # 调用父类初始化
        super().__init__()
        
        self.klines = klines
        self.factors = factor_outputs or {}
        self.initial_balance = initial_balance
        self.max_leverage = max_leverage

        n_factors = len(self.factors) if self.factors else 0

        # 观察空间：因子值(n) + [balance_ratio, position, unrealized_pnl, leverage]
        obs_size = max(n_factors + 4, 4)

        if HAS_GYM and spaces is not None:
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf,
                shape=(obs_size,), dtype=np.float32,
            )
            self.action_space = spaces.Box(
                low=np.array([-1.0, 0.0], dtype=np.float32),
                high=np.array([1.0, 1.0], dtype=np.float32),
                dtype=np.float32,
            )

        self._step_idx = 0
        self._obs_size = obs_size
        self.balance = initial_balance
        self.position = 0.0
        self.entry_price = 0.0
        self._pnl_history: list = []

    def reset(self, seed=None, options=None):
        """重置环境"""
        self._step_idx = 50  # 跳过前50根用于因子计算
        if self._step_idx >= len(self.klines):
            self._step_idx = 0
        self.balance = self.initial_balance
        self.position = 0.0
        self.entry_price = 0.0
        self._pnl_history = []
        return self._get_obs(), {}

    def step(self, action):
        """执行一步"""
        if isinstance(action, np.ndarray):
            direction = float(action[0])
            size = float(action[1])
        else:
            direction, size = float(action[0]), float(action[1])

        current_price = float(self.klines['close'].iloc[self._step_idx])
        reward = self._execute_action(direction, size, current_price)

        self._step_idx += 1
        done = self._step_idx >= len(self.klines) - 1
        truncated = False

        return self._get_obs(), reward, done, truncated, {}

    def _get_obs(self) -> np.ndarray:
        """获取观察值"""
        factor_vals = []
        for fid, series in self.factors.items():
            if self._step_idx < len(series):
                val = series.iloc[self._step_idx]
                factor_vals.append(float(val) if not pd.isna(val) else 0.0)
            else:
                factor_vals.append(0.0)

        account = [
            self.balance / self.initial_balance if self.initial_balance > 0 else 0,
            self.position,
            self._unrealized_pnl(),
            abs(self.position) * self.max_leverage,
        ]
        obs = np.array(factor_vals + account, dtype=np.float32)
        # Pad to obs_size if needed
        if len(obs) < self._obs_size:
            obs = np.pad(obs, (0, self._obs_size - len(obs)))
        return obs

    def _execute_action(self, direction: float, size: float, price: float) -> float:
        """执行交易动作"""
        target_position = direction * size * self.max_leverage
        delta = target_position - self.position

        if abs(delta) < 0.01:
            return 0.0

        # 平仓收益/亏损
        pnl = 0.0
        if self.position != 0:
            pnl = self.position * (price - self.entry_price)
            self.balance += pnl

        self.position = target_position
        self.entry_price = price if target_position != 0 else 0.0

        self._pnl_history.append(pnl)

        # 奖励 = PnL的Sharpe-like衡量
        return pnl / (self.initial_balance * 0.01 + 1e-10)

    def _unrealized_pnl(self) -> float:
        """未实现盈亏"""
        if self.position == 0 or self._step_idx >= len(self.klines):
            return 0.0
        current = float(self.klines['close'].iloc[self._step_idx])
        return self.position * (current - self.entry_price)

    @property
    def total_pnl(self) -> float:
        """累计盈亏"""
        return sum(self._pnl_history) if self._pnl_history else 0.0

    @property
    def sharpe_ratio(self) -> float:
        """简单Sharpe比率"""
        if len(self._pnl_history) < 2:
            return 0.0
        mean = np.mean(self._pnl_history)
        std = np.std(self._pnl_history)
        if std < 1e-10:
            return 0.0
        return float(mean / std * np.sqrt(len(self._pnl_history)))

    @property
    def current_step(self) -> int:
        return self._step_idx


# 如果 gymnasium 可用，重新定义为真正的 gym.Env 子类
if HAS_GYM:
    class _GymTradingEnv(gym.Env):
        """gymnasium 兼容的 TradingEnv 包装"""

        metadata = {'render_modes': []}

        def __init__(self, trading_env: TradingEnv):
            super().__init__()
            self._env = trading_env
            self.observation_space = trading_env.observation_space
            self.action_space = trading_env.action_space

        def reset(self, seed=None, options=None):
            return self._env.reset(seed=seed, options=options)

        def step(self, action):
            return self._env.step(action)
