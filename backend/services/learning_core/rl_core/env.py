"""TradingDecisionEnv — RL 交易决策环境（方案需求 3）

动作空间（离散 4）：
  0 = 持仓/观望 (hold)
  1 = 开多 (open_long)
  2 = 开空 (open_short)
  3 = 平仓 (close)

观测：统一因子向量（来自 FactorService 的归一化因子） + 持仓状态标志。
奖励：平仓时结算已实现收益率 - 交易成本；持仓时给极小时间惩罚；无效动作给小惩罚。

无第三方 RL 框架依赖（不依赖 gym/gymnasium），纯 Python + numpy 的 duck-typed 接口
（reset()/step()），可用于离线回测回放训练与在线增量 rollout。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 动作常量
ACT_HOLD = 0
ACT_OPEN_LONG = 1
ACT_OPEN_SHORT = 2
ACT_CLOSE = 3
ACTION_NAMES = {0: "hold", 1: "open_long", 2: "open_short", 3: "close"}
N_ACTIONS = 4


class TradingDecisionEnv:
    """基于因子快照序列 + 价格序列的交易决策环境。

    用法（离线回放）：
        env = TradingDecisionEnv(feature_seq, price_seq, symbol="BTC")
        obs = env.reset()
        while not done:
            action = policy.act(obs)
            obs, reward, done, info = env.step(action)
    """

    def __init__(
        self,
        feature_seq: List[Dict[str, float]],
        price_seq: List[float],
        *,
        symbol: str = "UNKNOWN",
        cost_pct: float = 0.0006,      # 单边成本（手续费+滑点）
        hold_penalty: float = 0.00005,  # 每 bar 持仓时间惩罚
        invalid_penalty: float = 0.001,  # 无效动作惩罚
    ) -> None:
        assert len(feature_seq) == len(price_seq), "feature/price 序列长度需一致"
        self.feature_seq = feature_seq
        self.price_seq = price_seq
        self.symbol = symbol
        self.cost_pct = cost_pct
        self.hold_penalty = hold_penalty
        self.invalid_penalty = invalid_penalty
        self.n_actions = N_ACTIONS

        self._i = 0
        self._position = 0       # 0 无仓 / +1 多 / -1 空
        self._entry_price = 0.0

    # ── 接口 ──

    def reset(self) -> Dict[str, Any]:
        self._i = 0
        self._position = 0
        self._entry_price = 0.0
        return self._obs()

    def step(self, action: int) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        price = self.price_seq[self._i]
        reward = 0.0
        info: Dict[str, Any] = {}

        if action == ACT_OPEN_LONG:
            if self._position == 0:
                self._position = 1
                self._entry_price = price
                reward -= self.cost_pct
            else:
                reward -= self.invalid_penalty
        elif action == ACT_OPEN_SHORT:
            if self._position == 0:
                self._position = -1
                self._entry_price = price
                reward -= self.cost_pct
            else:
                reward -= self.invalid_penalty
        elif action == ACT_CLOSE:
            if self._position != 0:
                ret = (price - self._entry_price) / (self._entry_price + 1e-12)
                reward += self._position * ret - self.cost_pct
                info["realized_return"] = self._position * ret
                self._position = 0
                self._entry_price = 0.0
            else:
                reward -= self.invalid_penalty
        else:  # HOLD
            if self._position != 0:
                reward -= self.hold_penalty

        # 前进一步
        self._i += 1
        done = self._i >= len(self.price_seq) - 1

        # 回合末强制平仓结算
        if done and self._position != 0:
            last_price = self.price_seq[-1]
            ret = (last_price - self._entry_price) / (self._entry_price + 1e-12)
            reward += self._position * ret - self.cost_pct
            info["forced_close_return"] = self._position * ret
            self._position = 0

        return self._obs(), reward, done, info

    # ── 内部 ──

    def _obs(self) -> Dict[str, Any]:
        idx = min(self._i, len(self.feature_seq) - 1)
        feat = dict(self.feature_seq[idx])
        feat["__position__"] = float(self._position)
        return feat


def build_env_from_klines(symbol: str, timeframe: str = "1h", bars: int = 300) -> Optional[TradingDecisionEnv]:
    """便捷构造：用 FactorService 逐段计算因子快照 + 收盘价，构建离线环境。

    说明：逐 bar 全量因子计算较重，这里采用滚动窗口的收盘价 + 末窗因子近似，
    主要用于生成额外训练转移；离线主训练仍以 ReplayBuffer（回测折算）为主。
    """
    try:
        import pandas as pd
        from backend.services.kline_data_service import kline_service
        from backend.services.factor_engine.factor_service import factor_service

        raw = kline_service.get_klines_from_db(symbol, timeframe, count=bars)
        if not raw or len(raw) < 60:
            return None
        df = pd.DataFrame(raw)
        prices = [float(x) for x in df["close"].tolist()]

        # 因子快照：用当前 FactorService 计算一次，作为整段近似特征（轻量）
        fv_map = factor_service.compute(symbol, timeframe)
        snap = {k: float(getattr(v, "normalized", 0.0)) for k, v in fv_map.items()}
        feature_seq = [dict(snap) for _ in prices]
        return TradingDecisionEnv(feature_seq, prices, symbol=symbol)
    except Exception as exc:
        logger.debug("[TradingDecisionEnv] build_env_from_klines 失败: %s", exc)
        return None
