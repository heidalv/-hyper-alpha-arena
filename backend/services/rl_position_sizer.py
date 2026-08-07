"""
RL Position Sizer — 强化学习仓位管理 (F3-4)

基于 SARSA (on-policy TD learning) 的仓位管理 agent，替代静态凯利公式。

State space (5维离散化):
  - market_regime: trending_up / trending_down / ranging / volatile / crash
  - volatility_ratio: low / normal / high / extreme (当前ATR vs 历史中位数)
  - drawdown_pct: 0% / 0-5% / 5-10% / 10-20% / >20%
  - consecutive_losses: 0 / 1 / 2 / 3 / 4+
  - win_streak: 0 / 1 / 2 / 3+

Action space (离散仓位比例 of equity):
  0, 0.05, 0.10, 0.15, 0.20, 0.25

Reward: rolling sharpe ratio over last 20 trades (delayed reward via SARSA)

训练: 离线使用历史回测数据，在线继续微调 (epsilon-greedy)
"""

import json
import logging
import math
import os
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 常量 ──
ACTIONS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25]  # 仓位比例（占账户权益）
N_ACTIONS = len(ACTIONS)

# 学习参数
ALPHA = 0.10         # 学习率
GAMMA = 0.95         # 折扣因子
EPSILON_START = 0.30 # 初始探索率
EPSILON_MIN = 0.05   # 最小探索率
EPSILON_DECAY = 0.9995  # 每次衰减因子

# 滚动窗口大小（用于计算 reward）
REWARD_WINDOW = 20

# Q-table 持久化路径
Q_TABLE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "rl_q_table.json")


def _discretize_regime(regime: str) -> int:
    """市场状态离散化 → 0-4"""
    mapping = {
        "trending_up": 0,
        "trending_down": 1,
        "ranging": 2,
        "volatile": 3,
        "crash": 4,
    }
    return mapping.get((regime or "").lower().replace(" ", "_"), 2)


def _discretize_volatility_ratio(ratio: float) -> int:
    """波动率比离散化 → 0-3"""
    if ratio <= 0:
        return 1  # unknown → normal
    if ratio < 0.5:
        return 0  # low
    if ratio <= 1.5:
        return 1  # normal
    if ratio <= 2.0:
        return 2  # high
    return 3  # extreme


def _discretize_drawdown(pct: float) -> int:
    """回撤百分比离散化 → 0-4"""
    if pct <= 0:
        return 0  # no drawdown
    if pct < 0.05:
        return 1
    if pct < 0.10:
        return 2
    if pct < 0.20:
        return 3
    return 4


def _discretize_consecutive_losses(n: int) -> int:
    """连续亏损数离散化 → 0-4"""
    return min(n, 4)


def _discretize_win_streak(n: int) -> int:
    """连胜数离散化 → 0-3"""
    return min(n, 3)


StateTuple = Tuple[int, int, int, int, int]


def encode_state(
    regime: str,
    volatility_ratio: float,
    drawdown_pct: float,
    consecutive_losses: int,
    win_streak: int,
) -> StateTuple:
    """将原始状态编码为离散元组"""
    return (
        _discretize_regime(regime),
        _discretize_volatility_ratio(volatility_ratio),
        _discretize_drawdown(drawdown_pct),
        _discretize_consecutive_losses(consecutive_losses),
        _discretize_win_streak(win_streak),
    )


@dataclass
class RLActionResult:
    """RL agent 返回的仓位建议"""
    position_pct: float          # 建议仓位比例 (0.0-0.25)
    action_index: int            # 选择的 action 索引
    q_value: float               # 所选 action 的 Q 值
    epsilon: float               # 当前探索率
    state: StateTuple            # 当前状态
    exploration: bool = False    # 是否为探索动作


class RlPositionSizer:
    """SARSA 强化学习仓位管理器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._q_table: Dict[StateTuple, List[float]] = {}  # state → [q0, q1, ..., q5]
        self._epsilon = EPSILON_START

        # 滚动盈亏记录用于计算 Sharpe reward
        self._pnl_window: deque = deque(maxlen=REWARD_WINDOW)

        # 训练统计
        self._episodes: int = 0
        self._total_steps: int = 0
        self._last_state: Optional[StateTuple] = None
        self._last_action_idx: Optional[int] = None

        # 加载已有 Q-table
        self._load()

        logger.info(
            f"[RLPositionSizer] 初始化完成: "
            f"states={len(self._q_table)} epsilon={self._epsilon:.3f}"
        )

    # ══════════════════════════════════════════════════
    #  公开接口
    # ══════════════════════════════════════════════════

    def select_action(
        self,
        regime: str,
        volatility_ratio: float,
        drawdown_pct: float,
        consecutive_losses: int,
        win_streak: int,
        use_greedy: bool = False,
    ) -> RLActionResult:
        """
        根据当前状态选择仓位比例。

        Args:
            regime: 市场状态 (trending_up/trending_down/ranging/volatile/crash)
            volatility_ratio: 当前ATR / 历史ATR中位数
            drawdown_pct: 当前回撤百分比 (0.0-1.0)
            consecutive_losses: 连续亏损次数
            win_streak: 连续盈利次数
            use_greedy: True=纯贪婪(回测/评估), False=epsilon-greedy(在线训练)

        Returns:
            RLActionResult with position_pct and metadata
        """
        state = encode_state(
            regime, volatility_ratio, drawdown_pct, consecutive_losses, win_streak
        )

        with self._lock:
            q_values = self._get_q_values(state)
            exploration = False

            if use_greedy or random.random() > self._epsilon:
                # 贪婪：选 Q 值最大的 action
                action_idx = max(range(N_ACTIONS), key=lambda i: q_values[i])
            else:
                # 探索：随机选 action
                action_idx = random.randrange(N_ACTIONS)
                exploration = True

            position_pct = ACTIONS[action_idx]

            # 强安全约束：连续亏损>=5 或 回撤>20% 时强制仓位=0
            if consecutive_losses >= 5 or drawdown_pct >= 0.20:
                action_idx = 0
                position_pct = 0.0
                exploration = False

            # 记录当前状态用于后续 SARSA 更新
            self._last_state = state
            self._last_action_idx = action_idx

        return RLActionResult(
            position_pct=position_pct,
            action_index=action_idx,
            q_value=q_values[action_idx],
            epsilon=self._epsilon,
            state=state,
            exploration=exploration,
        )

    def update(
        self,
        reward: float,
        next_regime: str = "",
        next_volatility_ratio: float = 0.0,
        next_drawdown_pct: float = 0.0,
        next_consecutive_losses: int = 0,
        next_win_streak: int = 0,
        done: bool = False,
    ) -> None:
        """
        SARSA 更新：基于 (S, A, R, S', A') 更新 Q 值。

        应在每笔交易平仓后调用。
        """
        with self._lock:
            if self._last_state is None or self._last_action_idx is None:
                return

            s = self._last_state
            a = self._last_action_idx

            # 衰减 epsilon
            self._epsilon = max(EPSILON_MIN, self._epsilon * EPSILON_DECAY)

            if done:
                # 终态：Q(S', A') = 0
                q_next = 0.0
            else:
                next_state = encode_state(
                    next_regime, next_volatility_ratio, next_drawdown_pct,
                    next_consecutive_losses, next_win_streak,
                )
                next_q = self._get_q_values(next_state)
                # SARSA: 使用实际选择的下一 action (此处用 greedy 近似)
                next_a = max(range(N_ACTIONS), key=lambda i: next_q[i])
                q_next = next_q[next_a]

            # TD 更新
            q_sa = self._get_q_values(s)[a]
            td_target = reward + GAMMA * q_next
            td_error = td_target - q_sa
            new_q = q_sa + ALPHA * td_error

            self._q_table.setdefault(s, [0.0] * N_ACTIONS)[a] = round(new_q, 6)

            self._total_steps += 1
            self._last_state = None
            self._last_action_idx = None

            # 周期性持久化（每 100 步）
            if self._total_steps % 100 == 0:
                self._save()

    def record_trade_result(self, pnl_pct: float) -> float:
        """记录交易结果并返回滚动 Sharpe (作为 reward)。

        Returns:
            rolling_sharpe: 最近 N 笔的 Sharpe ratio（年化近似）
        """
        with self._lock:
            self._pnl_window.append(pnl_pct)
            return self._compute_rolling_sharpe()

    def _compute_rolling_sharpe(self) -> float:
        """计算滚动窗口的 Sharpe ratio"""
        if len(self._pnl_window) < 3:
            return 0.0
        values = list(self._pnl_window)
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1)
        std = math.sqrt(variance) if variance > 0 else 1e-6
        return mean / std if std > 0 else 0.0

    # ══════════════════════════════════════════════════
    #  Q-table 管理
    # ══════════════════════════════════════════════════

    def _get_q_values(self, state: StateTuple) -> List[float]:
        """获取状态的 Q 值向量（不存在则初始化为 0）"""
        if state not in self._q_table:
            self._q_table[state] = [0.0] * N_ACTIONS
        return self._q_table[state]

    def _save(self) -> None:
        """持久化 Q-table 到 JSON 文件"""
        try:
            os.makedirs(os.path.dirname(Q_TABLE_PATH), exist_ok=True)
            serializable = {
                "_".join(str(x) for x in state): values
                for state, values in self._q_table.items()
            }
            data = {
                "q_table": serializable,
                "epsilon": self._epsilon,
                "total_steps": self._total_steps,
                "episodes": self._episodes,
                "saved_at": time.time(),
            }
            with open(Q_TABLE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"[RLPositionSizer] Q-table 已保存 ({len(self._q_table)} states)")
        except Exception as e:
            logger.warning(f"[RLPositionSizer] Q-table 保存失败: {e}")

    def _load(self) -> None:
        """从 JSON 文件加载 Q-table"""
        if not os.path.exists(Q_TABLE_PATH):
            logger.info("[RLPositionSizer] 无已有 Q-table，初始化为空")
            return
        try:
            with open(Q_TABLE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = 0
            for key_str, values in data.get("q_table", {}).items():
                state = tuple(int(x) for x in key_str.split("_"))
                if len(state) == 5 and len(values) == N_ACTIONS:
                    self._q_table[state] = values
                    loaded += 1
            self._epsilon = data.get("epsilon", EPSILON_START)
            self._total_steps = data.get("total_steps", 0)
            self._episodes = data.get("episodes", 0)
            logger.info(
                f"[RLPositionSizer] Q-table 已加载: "
                f"{loaded} states, epsilon={self._epsilon:.3f}, steps={self._total_steps}"
            )
        except Exception as e:
            logger.warning(f"[RLPositionSizer] Q-table 加载失败: {e}")

    def reset(self) -> None:
        """重置 Q-table（用于重新训练）"""
        with self._lock:
            self._q_table.clear()
            self._epsilon = EPSILON_START
            self._total_steps = 0
            self._episodes = 0
            self._pnl_window.clear()
            self._last_state = None
            self._last_action_idx = None
            logger.info("[RLPositionSizer] Q-table 已重置")

    # ══════════════════════════════════════════════════
    #  离线训练
    # ══════════════════════════════════════════════════

    def train_on_history(
        self,
        trades: List[Dict[str, Any]],
        episodes: int = 1000,
    ) -> Dict[str, Any]:
        """使用历史交易数据离线训练。

        Args:
            trades: 历史交易列表，每条包含:
                - pnl_pct, regime, volatility_ratio, drawdown_pct,
                  consecutive_losses, win_streak
            episodes: 训练轮数

        Returns:
            训练统计
        """
        if len(trades) < REWARD_WINDOW:
            return {"error": "训练数据不足", "min_trades": REWARD_WINDOW}

        start_time = time.time()
        total_reward = 0.0

        for ep in range(episodes):
            episode_reward = 0.0
            # 滑动窗口训练
            window = deque(maxlen=REWARD_WINDOW)
            self._last_state = None
            self._last_action_idx = None

            for i, trade in enumerate(trades):
                regime = trade.get("regime", "ranging")
                vol_ratio = float(trade.get("volatility_ratio", 1.0))
                dd_pct = float(trade.get("drawdown_pct", 0.0))
                cons_loss = int(trade.get("consecutive_losses", 0))
                win_streak = int(trade.get("win_streak", 0))
                pnl_pct = float(trade.get("pnl_pct", 0.0))

                # 选择 action
                action_result = self.select_action(
                    regime, vol_ratio, dd_pct, cons_loss, win_streak,
                    use_greedy=False,
                )

                # 模拟 reward: 仓位 × 实际盈亏
                simulated_reward = action_result.position_pct * pnl_pct / 0.05  # 归一化到基准仓位

                window.append(pnl_pct)

                # 计算下个状态
                next_idx = i + 1
                done = next_idx >= len(trades)
                if not done:
                    next_t = trades[next_idx]
                    self.update(
                        simulated_reward,
                        next_regime=next_t.get("regime", "ranging"),
                        next_volatility_ratio=float(next_t.get("volatility_ratio", 1.0)),
                        next_drawdown_pct=float(next_t.get("drawdown_pct", 0.0)),
                        next_consecutive_losses=int(next_t.get("consecutive_losses", 0)),
                        next_win_streak=int(next_t.get("win_streak", 0)),
                        done=False,
                    )
                else:
                    self.update(simulated_reward, done=True)

                episode_reward += simulated_reward

            self._episodes += 1
            total_reward += episode_reward

            # 每 10 轮打印进度
            if (ep + 1) % 100 == 0:
                logger.info(
                    f"[RLPositionSizer] 训练进度: {ep + 1}/{episodes} "
                    f"avg_reward={total_reward/(ep+1):.4f} "
                    f"epsilon={self._epsilon:.4f} "
                    f"states={len(self._q_table)}"
                )

        self._save()

        elapsed = time.time() - start_time
        stats = {
            "episodes": episodes,
            "total_steps": self._total_steps,
            "states_learned": len(self._q_table),
            "epsilon_final": self._epsilon,
            "avg_reward_per_episode": round(total_reward / episodes, 6),
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info(f"[RLPositionSizer] 离线训练完成: {stats}")
        return stats

    # ══════════════════════════════════════════════════
    #  统计分析
    # ══════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """返回当前 RL agent 统计信息"""
        with self._lock:
            q_values_all = [max(v) for v in self._q_table.values()] if self._q_table else [0]
            return {
                "states_learned": len(self._q_table),
                "epsilon": round(self._epsilon, 4),
                "total_steps": self._total_steps,
                "episodes": self._episodes,
                "rolling_sharpe": round(self._compute_rolling_sharpe(), 4),
                "max_q_value": round(max(q_values_all), 4),
                "mean_q_value": round(sum(q_values_all) / max(len(q_values_all), 1), 4),
                "q_table_path": Q_TABLE_PATH,
            }

    def get_action_distribution(self) -> Dict[str, int]:
        """统计 Q-table 中各 action 的贪婪选择频率"""
        counts = {str(a): 0 for a in ACTIONS}
        with self._lock:
            for state, q_values in self._q_table.items():
                best_a = max(range(N_ACTIONS), key=lambda i: q_values[i])
                counts[str(ACTIONS[best_a])] += 1
        return counts

    def get_top_q_states(self, n: int = 10) -> List[Dict[str, Any]]:
        """返回 Q 值最高的 N 个状态（用于调试/分析）"""
        with self._lock:
            scored = [
                {
                    "state": list(s),
                    "q_values": [round(v, 4) for v in qv],
                    "best_action": ACTIONS[max(range(N_ACTIONS), key=lambda i: qv[i])],
                    "max_q": round(max(qv), 4),
                }
                for s, qv in self._q_table.items()
            ]
            scored.sort(key=lambda x: x["max_q"], reverse=True)
            return scored[:n]


# ── 全局单例 ──
_rl_position_sizer: Optional[RlPositionSizer] = None


def get_rl_position_sizer() -> RlPositionSizer:
    """获取 RL 仓位管理器单例"""
    global _rl_position_sizer
    if _rl_position_sizer is None:
        _rl_position_sizer = RlPositionSizer()
    return _rl_position_sizer
