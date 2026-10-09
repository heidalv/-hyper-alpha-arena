# -*- coding: utf-8 -*-
"""[h891] 统一学习进化内核 —— 三层(HFT/MID/LONG)共用的骨架(M1)。

用户要求:FinRL 式的学习进化整合 —— 高频/中线/长线各一个影子学习者,
共用一个进化内核(环境接口 + 影子训练 + 统一晋级门),三层互相喂信号。

本模块提供:
  1. HorizonEnv        —— gym 风格环境抽象(三层各自实现)
  2. HftShadowEnv      —— 高频影子环境(包装现有 active_flow 决策 + 往返账本 reward)
  3. MidShadowEnv / LongShadowEnv —— 中线/长线影子环境(骨架,数据聚合待接)
  4. ShadowLearner     —— FinRL 式学习者接口(PPO/SAC 可插拔;默认最小 PPO 训练循环)
  5. PromotionGate     —— 统一晋级门(与 should_rollback_flow 同款三关 + OOS 小时数)

纪律:任何影子学习者 OOS 连续 N 小时不输基准才晋升(数据已证 tick 级 markout≈0,
RL 极易过拟合 —— 晋级门是唯一的闸)。
"""
from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = Path(os.getenv("HYPER_ALPHA_ROOT", r"D:\001Alpha\Hyper-Alpha-Arena"))


# ══════════════════════════════════════════════════════════════════════
# 1. 环境抽象
# ══════════════════════════════════════════════════════════════════════
class HorizonEnv(ABC):
    """三层环境共同接口。obs 是共享特征表的三套聚合。"""

    era: str = "hft"

    @abstractmethod
    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        """回到一个 episode 起点,返回初始 obs。"""

    @abstractmethod
    def step(self, action: np.ndarray) -> tuple:
        """返回 (obs, reward, done, info)。reward 用"往返 y(bp)"口径,与账本一致。"""

    @property
    @abstractmethod
    def obs_dim(self) -> int: ...

    @property
    @abstractmethod
    def act_dim(self) -> int: ...


# ══════════════════════════════════════════════════════════════════════
# 2. 高频影子环境(包现有 active_flow 决策;reward=往返 y)
# ══════════════════════════════════════════════════════════════════════
class HftShadowEnv(HorizonEnv):
    """高频环境：obs = 入场语境 10 维；action = 做/歇（0=挂双边 1=休息）。

    [2026-10-09 进化重挂] 旧动作空间是「方向×价位档×名义档」（方向模型口径），
    新机器不猜方向：空仓时挂双边是唯一动作，剩下可学的是**该拍做不做**。
    reward = 往返 rt_bp（做）/ 0（歇）——反事实口径与生产机器一致：
    歇掉的那一拍不赚不亏。
    """

    era = "hft"
    OBS_KEYS = ("entry_spread_bp", "entry_vol_bp", "entry_front_usd",
                "regime_rank", "gap_flag", "hour", "dow", "hold_sec",
                "same_side_n", "pad")

    def __init__(self, data_root: Optional[Path] = None):
        self.root = data_root or ROOT
        self._episodes: List[Dict[str, Any]] = []
        self._i = 0
        self._cur: Dict[str, Any] = {}

    @property
    def obs_dim(self) -> int:
        return len(self.OBS_KEYS)

    @property
    def act_dim(self) -> int:
        # 0=做(挂双边) 1=歇
        return 2

    def _load_episodes(self):
        p = self.root / "data" / "flow_roundtrip_log.jsonl"
        if not p.exists():
            self._episodes = []
            return
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
                if x.strip()]
        self._episodes = [r for r in rows
                          if r.get("y_bp") is not None
                          and str(r.get("strategy") or "") == "PP"]

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        rng = np.random.default_rng(seed)
        self._load_episodes()
        if not self._episodes:
            self._i, self._cur = 0, {}
            return np.zeros(self.obs_dim, dtype=np.float32)
        self._i = int(rng.integers(0, max(1, len(self._episodes) - 1)))
        self._cur = self._episodes[self._i]
        return self._obs_from(self._cur)

    def _obs_from(self, r: Dict[str, Any]) -> np.ndarray:
        _r = {"R1": 1.0, "R2": 2.0, "R3": 3.0, "R4": 4.0, "R5": 5.0}
        try:
            _lt = time.localtime(float(r.get("ts") or 0.0))
        except Exception:
            _lt = time.localtime(0)
        obs = [
            float(r.get("entry_spread_bp") or 0.0),
            float(r.get("entry_vol_bp") or 0.0),
            float(r.get("entry_front_usd") or 0.0) / 1000.0,
            float(_r.get(str(r.get("entry_regime") or ""), 0.0)),
            1.0 if r.get("gap") else 0.0,
            float(_lt.tm_hour % 24),
            float(_lt.tm_wday % 7),
            float(r.get("hold_sec") or 0.0),
            float(r.get("same_side_n") or 0.0),
            0.0,
        ]
        return np.asarray(obs, dtype=np.float32)

    def step(self, action: np.ndarray) -> tuple:
        a = int(np.argmax(action)) if action.ndim > 0 else int(action)
        r = self._episodes[self._i] if self._i < len(self._episodes) else {}
        # 0=做 ⇒ 历史 rt_bp；1=歇 ⇒ 0（不赚不亏）
        reward = float(r.get("y_bp") or 0.0) if a == 0 else 0.0
        self._i += 1
        if self._i >= len(self._episodes) - 1:
            done = True
            obs = np.zeros(self.obs_dim, dtype=np.float32)
        else:
            done = False
            self._cur = self._episodes[self._i]
            obs = self._obs_from(self._cur)
        info = {"a": a, "y_bp": reward, "era": self.era}
        return obs, reward, done, info


# ══════════════════════════════════════════════════════════════════════
# 3. 中线 / 长线影子环境(骨架;数据聚合在 M3/M4 接)
# ══════════════════════════════════════════════════════════════════════
class MidShadowEnv(HorizonEnv):
    """中线(15min):obs = 15min 流 + 资金费 + 跨币宽度 + 高频流聚合;
    action = liq_reversal 进出场(0=空 1=平 2=多)。"""

    era = "mid"

    @property
    def obs_dim(self) -> int:
        return 12

    @property
    def act_dim(self) -> int:
        return 3

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        return np.zeros(self.obs_dim, dtype=np.float32)

    def step(self, action: np.ndarray) -> tuple:
        return np.zeros(self.obs_dim, dtype=np.float32), 0.0, True, {"era": self.era}


class LongShadowEnv(HorizonEnv):
    """长线(日级):obs = 日线趋势 + 资金费 + 仓位 + 高/中线先验;
    action = carry_basis 仓位档(0~3)。"""

    era = "long"

    @property
    def obs_dim(self) -> int:
        return 10

    @property
    def act_dim(self) -> int:
        return 4

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        return np.zeros(self.obs_dim, dtype=np.float32)

    def step(self, action: np.ndarray) -> tuple:
        return np.zeros(self.obs_dim, dtype=np.float32), 0.0, True, {"era": self.era}


# ══════════════════════════════════════════════════════════════════════
# 4. FinRL 式学习者(最小 PPO;算法可换成 FinRL 的 agent 实现)
# ══════════════════════════════════════════════════════════════════════
@dataclass
class ShadowLearner:
    """最小 PPO 骨架。生产版用 FinRL/ElegantRL 的 agent 代码替换本类。"""

    env: HorizonEnv
    lr: float = 3e-4
    gamma: float = 0.99
    clip: float = 0.2
    epochs: int = 4
    hidden: int = 64
    device: str = "cpu"
    name: str = "shadow"

    _params: Dict[str, np.ndarray] = field(default_factory=dict, init=False)

    def _init_net(self):
        rng = np.random.default_rng(0)
        s = (self.env.obs_dim, self.hidden)
        a = (self.hidden, self.env.act_dim)
        self._params = {
            "w1": rng.standard_normal(s) * np.sqrt(1.0 / self.env.obs_dim),
            "b1": np.zeros(self.hidden),
            "w2": rng.standard_normal(a) * np.sqrt(1.0 / self.hidden),
            "b2": np.zeros(self.env.act_dim),
            "wv": rng.standard_normal((self.hidden, 1)) * 0.01,
            "bv": np.zeros(1),
        }

    def _forward(self, obs: np.ndarray) -> tuple:
        h = np.tanh(obs @ self._params["w1"] + self._params["b1"])
        logits = h @ self._params["w2"] + self._params["b2"]
        v = float((h @ self._params["wv"] + self._params["bv"])[0])
        return logits, v

    def policy(self, obs: np.ndarray) -> tuple:
        """返回 (动作 one-hot, logp)。骨架用 softmax 采样。"""
        logits, _ = self._forward(obs)
        z = np.exp(logits - np.max(logits))
        p = z / (z.sum() + 1e-12)
        a = np.random.choice(self.env.act_dim, p=p)
        onehot = np.zeros(self.env.act_dim, dtype=np.float32)
        onehot[a] = 1.0
        return onehot, float(np.log(p[a] + 1e-12))

    def train(self, steps: int = 200, horizon: int = 64,
              log_every: int = 50) -> List[float]:
        """最小 PPO 训练循环。产出每轮平均 reward 序列(诊断用)。"""
        if not self._params:
            self._init_net()
        history: List[float] = []
        for ep in range(steps // horizon or 1):
            obs = self.env.reset()
            buf_obs, buf_act, buf_logp, buf_r, buf_v, buf_done = [], [], [], [], [], []
            total = 0.0
            for _ in range(horizon):
                a, logp = self.policy(obs)
                nxt, r, done, _ = self.env.step(a)
                _, v = self._forward(obs)
                buf_obs.append(obs)
                buf_act.append(a)
                buf_logp.append(logp)
                buf_r.append(r)
                buf_v.append(v)
                buf_done.append(float(done))
                total += r
                obs = nxt
                if done:
                    obs = self.env.reset()
            history.append(total / max(1, horizon))
            if ep % log_every == 0:
                print(f"[shadow:{self.name}] ep {ep} avg_r {history[-1]:+.3f}",
                      flush=True)
        return history


# ══════════════════════════════════════════════════════════════════════
# 4b. 真正的 PPO 学习者(h893,PyTorch 实现)—— 与 ShadowLearner 同接口的即插替换
# ══════════════════════════════════════════════════════════════════════
class TorchPPO:
    """[h893 2026-10-07] 真 PPO 学习者。与 numpy 骨架 ShadowLearner 的**本质区别**:
    train() 真的做梯度更新(rollout → GAE 优势 → PPO clip 损失 → Adam),
    骨架只收集轨迹从不更新参数 ⇒ 骨架"学"多久策略都停在随机初始网络。

    设计纪律(与三层架构文档一致):
      · 算法借 FinRL 思路(PPO clip + GAE + entropy 正则),实现自己写
        —— 零新依赖(torch 2.6 已装),完全透明可审计;
      · 影子学习,只在离线脚本里跑,绝不触碰生产 worker;
      · 是否晋升由 promote_gate 裁决,不由训练曲线裁决(训练 reward 上升
        只证明"能拟合",不证明"有 edge"——tick 级 markout≈0,RL 极易过拟合)。
    """

    def __init__(self, env: HorizonEnv, lr: float = 3e-4, gamma: float = 0.99,
                 lam: float = 0.95, clip: float = 0.2, ent_coef: float = 0.01,
                 vf_coef: float = 0.5, epochs: int = 4, minibatch: int = 256,
                 hidden: int = 64, device: Optional[str] = None,
                 name: str = "ppo", seed: int = 0, max_grad_norm: float = 0.5):
        import torch  # 延迟导入:模块本身不依赖 torch(骨架类仍可裸跑)
        self._t = torch
        self.env = env
        self.gamma, self.lam = float(gamma), float(lam)
        self.clip = float(clip)
        self.ent_coef = float(ent_coef)
        self.vf_coef = float(vf_coef)
        self.epochs = int(epochs)
        self.minibatch = int(minibatch)
        self.max_grad_norm = float(max_grad_norm)
        self.name = name
        # 小网络 CPU 足够且可复现;显式传 device="cuda" 才上显卡
        self._dev = device or "cpu"
        torch.manual_seed(seed)
        np.random.seed(seed)

        def _mlp(d_in: int, d_out: int):
            return torch.nn.Sequential(
                torch.nn.Linear(d_in, hidden), torch.nn.Tanh(),
                torch.nn.Linear(hidden, hidden), torch.nn.Tanh(),
                torch.nn.Linear(hidden, d_out),
            ).to(self._dev)

        self._actor = _mlp(env.obs_dim, env.act_dim)     # 策略头(离散动作)
        self._critic = _mlp(env.obs_dim, 1)              # 价值头
        self._opt = torch.optim.Adam(
            list(self._actor.parameters()) + list(self._critic.parameters()), lr=lr)
        self.last_train_stats: Dict[str, float] = {}

    # ── 内部:单次前向(采样 + 价值) ──
    def _sample(self, obs: np.ndarray) -> tuple:
        torch = self._t
        with torch.no_grad():
            o = torch.as_tensor(np.asarray(obs, dtype=np.float32), device=self._dev)
            logits = self._actor(o)
            v = float(self._critic(o).squeeze(-1).item())
            probs = torch.softmax(logits, dim=-1)
            a = int(torch.multinomial(probs, 1).item())
            logp = float(torch.log(probs[a] + 1e-12).item())
        return a, logp, v

    # ── 与 ShadowLearner 相同的对外接口 ──
    def policy(self, obs: np.ndarray) -> tuple:
        """采样动作(训练用)。返回 (one-hot, logp),与 numpy 骨架同口径。"""
        a, logp, _ = self._sample(obs)
        onehot = np.zeros(self.env.act_dim, dtype=np.float32)
        onehot[a] = 1.0
        return onehot, logp

    def act_greedy(self, obs: np.ndarray) -> int:
        """确定性策略(OOS 评估/影子部署用):argmax 概率,不采样。"""
        torch = self._t
        with torch.no_grad():
            o = torch.as_tensor(np.asarray(obs, dtype=np.float32), device=self._dev)
            return int(torch.argmax(self._actor(o), dim=-1).item())

    def train(self, steps: int = 2048, horizon: int = 256,
              log_every: int = 8) -> List[float]:
        """真 PPO 训练循环。返回每轮平均 reward 序列(诊断用)。"""
        torch = self._t
        history: List[float] = []
        n_iter = max(1, int(steps) // max(1, int(horizon)))
        for it in range(n_iter):
            # ── rollout:用当前策略收集一批轨迹 ──
            obs = self.env.reset()
            b_obs: List[np.ndarray] = []
            b_act: List[int] = []
            b_logp: List[float] = []
            b_rew: List[float] = []
            b_val: List[float] = []
            b_done: List[float] = []
            for _ in range(int(horizon)):
                a, logp, v = self._sample(obs)
                onehot = np.zeros(self.env.act_dim, dtype=np.float32)
                onehot[a] = 1.0
                nxt, r, done, _ = self.env.step(onehot)
                b_obs.append(np.asarray(obs, dtype=np.float32))
                b_act.append(a)
                b_logp.append(logp)
                b_rew.append(float(r))
                b_val.append(v)
                b_done.append(float(done))
                obs = self.env.reset() if done else nxt
            history.append(float(np.mean(b_rew)) if b_rew else 0.0)

            # ── GAE(γ,λ) 优势估计 ──
            with torch.no_grad():  # 末端状态的价值 bootstrap(horizon 截断时用)
                _o = torch.as_tensor(np.asarray(obs, dtype=np.float32),
                                     device=self._dev)
                last_v = float(self._critic(_o).squeeze(-1).item())
            T = len(b_rew)
            adv = np.zeros(T, dtype=np.float32)
            gae = 0.0
            for t in range(T - 1, -1, -1):
                next_v = b_val[t + 1] if t + 1 < T else last_v
                delta = b_rew[t] + self.gamma * next_v * (1.0 - b_done[t]) - b_val[t]
                gae = delta + self.gamma * self.lam * (1.0 - b_done[t]) * gae
                adv[t] = gae
            ret = adv + np.asarray(b_val, dtype=np.float32)
            adv = (adv - adv.mean()) / (float(adv.std()) + 1e-8)

            obs_t = torch.as_tensor(np.asarray(b_obs), device=self._dev)
            act_t = torch.as_tensor(np.asarray(b_act), dtype=torch.int64,
                                    device=self._dev)
            old_logp_t = torch.as_tensor(np.asarray(b_logp), device=self._dev)
            adv_t = torch.as_tensor(adv, device=self._dev)
            ret_t = torch.as_tensor(ret, device=self._dev)

            # ── PPO clip 更新(minibatch × epochs) ──
            ent_m = kl_m = 0.0
            params = list(self._actor.parameters()) + list(self._critic.parameters())
            for _ in range(self.epochs):
                perm = torch.randperm(T, device=self._dev)
                for mb in perm.split(self.minibatch):
                    logits = self._actor(obs_t[mb])
                    dist = torch.distributions.Categorical(logits=logits)
                    logp = dist.log_prob(act_t[mb])
                    ratio = torch.exp(logp - old_logp_t[mb])
                    s1 = ratio * adv_t[mb]
                    s2 = torch.clamp(ratio, 1.0 - self.clip,
                                     1.0 + self.clip) * adv_t[mb]
                    loss_pi = -torch.min(s1, s2).mean()
                    v_pred = self._critic(obs_t[mb]).squeeze(-1)
                    loss_v = torch.nn.functional.mse_loss(v_pred, ret_t[mb])
                    ent = dist.entropy().mean()
                    loss = loss_pi + self.vf_coef * loss_v - self.ent_coef * ent
                    self._opt.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(params, self.max_grad_norm)
                    self._opt.step()
                    ent_m = float(ent.item())
                    with torch.no_grad():
                        kl_m = float((old_logp_t[mb] - logp).mean().item())
            self.last_train_stats = {"entropy": ent_m, "approx_kl": kl_m,
                                     "mean_rew": history[-1], "iter": it}
            if log_every and it % log_every == 0:
                print(f"[ppo:{self.name}] it {it} avg_r {history[-1]:+.3f} "
                      f"H={ent_m:.3f} kl={kl_m:.4f}", flush=True)
        return history


# ══════════════════════════════════════════════════════════════════════
# 5. 统一晋级门(与 should_rollback_flow 同款三关 + OOS 小时数)
# ══════════════════════════════════════════════════════════════════════
def promote_gate(before: Dict[str, float], after: Dict[str, float],
                 min_hours: float = 2.0) -> tuple:
    """统一晋级裁决(三层通用)。

    before/after 字段:n, mean_y, taker_fee_share, fill_rate, liquidations。
    晋升条件:①样本 ≥30 ②平均 y 不显著劣于基准 ③吃单费占比不升 ④挂单率不塌
             ⑤强平=0 ⑥after 的时长 ≥ min_hours。

    [h893b 2026-10-07] ②的统一语义:**影子不得比基准差超过 max(20%|基准|, 0.05bp)**。
    旧逻辑 `before>0 and after < before×0.8` 在**基准为负时完全漏检**
    (实测:长线基准 −0.65bp、影子 −1.76bp 仍判 promote ✗)。
    新形式在基准为正时与原逻辑一致(0.8×before = before − 0.2×before),
    基准为负时要求"不得再差 20%|基准|",基准≈0 时由 0.05bp 地板兜底。
    """
    if float(after.get("n") or 0) < 30:
        return False, f"n={after.get('n')}<30"
    b = float(before.get("mean_y") or 0.0)
    a = float(after.get("mean_y") or 0.0)
    tol = max(0.2 * abs(b), 0.05)
    if a < b - tol:
        return False, f"mean_y {a:+.2f} < 基准{b:+.2f}−容忍{tol:.2f}"
    if float(after.get("taker_fee_share") or 0) > float(before.get("taker_fee_share") or 0) + 0.05:
        return False, "taker_fee_share 上升"
    if float(after.get("fill_rate") or 0) < float(before.get("fill_rate") or 0) - 0.15:
        return False, "fill_rate 塌"
    if float(after.get("liquidations") or 0) > 0:
        return False, "liquidations>0"
    if float(after.get("hours") or 0) < min_hours:
        return False, f"hours {after.get('hours')}<{min_hours}"
    return True, "promote"


# ══════════════════════════════════════════════════════════════════════
# 6. 三层互联(横向联系):长线 regime → 中线先验 → 高频否决
# ══════════════════════════════════════════════════════════════════════
def cross_horizon_prior(long_state: Optional[np.ndarray],
                        mid_state: Optional[np.ndarray],
                        hft_obs: np.ndarray,
                        base_veto_bp: float = 4.0) -> float:
    """自上而下的先验链:长线/中线的方向读数放大或缩小高频的趋势否决阈值。

    长线 regime 偏空 ⇒ 高频的逆空否决更严(阈值更小);
    中线无方向 ⇒ 高频回到基线阈值。返回本次高频的趋势否决阈值(bp)。
    """
    v = float(base_veto_bp)
    if long_state is not None and float(long_state[0]) < 0:      # 长线偏空
        v = max(1.0, v * 0.7)
    if mid_state is not None and abs(float(mid_state[0])) < 0.1:  # 中线无方向
        v = max(1.0, v * 1.3)
    return v


# ══════════════════════════════════════════════════════════════════════
# 8. M5 三层互联(影子版:只记录不执行,生产阈值不变)
# ══════════════════════════════════════════════════════════════════════
def compute_regime_readings(bars_long: Optional[Dict[str, Dict[str, np.ndarray]]],
                            bars_mid: Optional[Dict[str, Dict[str, np.ndarray]]],
                            ) -> tuple:
    """从长线/中线 bar 数据算「当前 regime 读数」(慢变量快照)。

    long_state[0] = 各币最新 48bar(≈12h)趋势 bp 的均值 —— 日级方向;
    mid_state[0]  = 各币最新 5bar(≈75min)收益 bp 的均值 —— 中线动量。
    近似(显式标注):regime 是慢变量,高频 6h 回放窗口内视为不变。
    """
    long_trends, mid_rets = [], []
    for _sym, b in (bars_long or {}).items():
        c = b["c"]
        if len(c) >= 49:
            long_trends.append(float(c[-2] / c[-49] - 1.0) * 1e4)
    for _sym, b in (bars_mid or {}).items():
        c = b["c"]
        if len(c) >= 6:
            mid_rets.append(float(c[-2] / c[-6] - 1.0) * 1e4)
    long_state = np.array([np.mean(long_trends) if long_trends else 0.0],
                          dtype=np.float32)
    mid_state = np.array([np.mean(mid_rets) if mid_rets else 0.0],
                         dtype=np.float32)
    return long_state, mid_state


# [h893c] m5_veto_bp 住在 m5_bridge.py(纯标准库)——模拟仓 worker 的
# .runtime 解释器没有 numpy,import 本模块会失败;worker 直接从 m5_bridge 取。
# 这里 re-export 供训练脚本/测试用,口径唯一来源是 m5_bridge。
from backend.services.evolution.m5_bridge import m5_veto_bp  # noqa: F401,E402


def eval_veto_thresholds(steps: List[Tuple[np.ndarray, float]],
                         static_bp: float, dyn_bp: float,
                         trend_idx: int = 4, ofi_idx: int = 1) -> Dict[str, Any]:
    """在高频历史 (obs, y) 上对比 静态 vs 动态 趋势否决阈值(影子,不执行)。

    否决规则(与生产 active_flow 一致):|trend_300s| ≥ 阈值 且 OFI 方向与趋势
    相反 ⇒ 不进场。被否决交易的历史 y 均值越负 ⇒ 否决越有效(拦住了亏损)。
    关键增量口径:
      · dyn_only     —— 动态阈值多拦的(静态没拦):其 mean_y<0 说明动态有增量价值
      · static_only  —— 静态拦了动态放行的:其 mean_y>0 说明放行对了
    """
    groups: Dict[str, List[float]] = {"static": [], "dynamic": [],
                                      "dyn_only": [], "static_only": []}
    for obs, y in steps:
        trend = float(obs[trend_idx])
        ofi = float(obs[ofi_idx])
        if ofi == 0.0 or trend == 0.0:
            continue
        contra = (ofi > 0) != (trend > 0)            # OFI 与趋势相反 = 逆势单
        s_veto = contra and abs(trend) >= static_bp
        d_veto = contra and abs(trend) >= dyn_bp
        if s_veto:
            groups["static"].append(y)
        if d_veto:
            groups["dynamic"].append(y)
        if d_veto and not s_veto:
            groups["dyn_only"].append(y)
        if s_veto and not d_veto:
            groups["static_only"].append(y)
    return {k: {"n": len(v),
                "mean_y": round(float(np.mean(v)), 3) if v else None}
            for k, v in groups.items()}


# ══════════════════════════════════════════════════════════════════════
# 7. 真实数据环境(h892):三层各自喂真实数据
# ══════════════════════════════════════════════════════════════════════
class HftReplayEnv(HorizonEnv):
    """高频真实回放:obs = load_hft_features 的 15 维;reward = 历史往返 y。

    OOS 对比:训练集取前半,评估集取后半(时间顺序切分,不做随机打乱)。

    [2026-10-09 进化重挂] `action_dependent=True` 时 reward 与动作挂钩
    (离线反事实近似):动作 ∈ {0=做, 1=歇}。
      · 做 ⇒ reward = 历史实得 rt_bp;
      · 歇 ⇒ reward = 0(不赚不亏 —— 歇掉的进场在历史里没有反事实)。
      ⚠ 这是**近似**:歇掉的那一拍若当时做,真实结果不可知。
      所以离线裁决只是烟测,真正的闸是 promote_gate 要求的实盘影子小时数。
    """

    era = "hft"

    def __init__(self, feats: Dict[str, Dict[str, np.ndarray]],
                 split: float = 0.7, train: bool = True,
                 action_dependent: bool = False, cf_discount: float = 0.5):
        import itertools
        self._steps: List[Tuple[np.ndarray, float]] = []
        for sym, d in feats.items():
            obs = d["obs"]
            y = d["y"]
            n = len(obs)
            cut = int(n * split)
            idx = range(0, cut) if train else range(cut, n)
            for i in idx:
                self._steps.append((obs[i], float(y[i])))
        self._rng = np.random.default_rng(0 if train else 999)
        self._i = 0
        self.action_dependent = bool(action_dependent)
        self.cf_discount = float(cf_discount)
        if not self._steps:
            self._steps = [(np.zeros(self.obs_dim, np.float32), 0.0)]

    @property
    def obs_dim(self) -> int:
        return 15

    @property
    def act_dim(self) -> int:
        # [2026-10-09 进化重挂] 0=做(挂双边) 1=歇。方向×档位×名义档的动作
        # 空间随 ping-pong 机器一起退役（每笔一样大、不猜方向）。
        return 2

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._i = 0
        return self._steps[0][0].astype(np.float32)

    def shape_reward(self, obs: np.ndarray, y: float, action: Any) -> tuple:
        """反事实奖励塑形(独立成方法,便于单测与审计)。返回 (reward, info)。

        [2026-10-09] 做 ⇒ 历史 rt_bp；歇 ⇒ 0（不赚不亏）。这是对生产机器的
        诚实近似：歇掉的进场那一边在历史里没有反事实，记为 0 表示"没吃到
        也没亏到"，不是"做反了"。
        """
        del obs
        a = int(np.argmax(action)) if np.ndim(action) > 0 else int(action)
        reward = float(y) if a == 0 else 0.0
        return reward, {"quote": a == 0, "a": a}

    def step(self, action: np.ndarray) -> tuple:
        cur_obs, y = self._steps[self._i]
        reward = float(y)
        extra: Dict[str, Any] = {}
        if self.action_dependent:
            reward, extra = self.shape_reward(cur_obs, float(y), action)
        self._i += 1
        if self._i >= len(self._steps):
            return (np.zeros(self.obs_dim, np.float32), reward, True,
                    {"era": self.era, **extra})
        obs = self._steps[self._i][0].astype(np.float32)
        return obs, reward, False, {"era": self.era, **extra}


class MidReplayEnv(HorizonEnv):
    """[h893 重写] 中线真实回放(15min):清算反转(liq_reversal)的影子环境。

    obs(12 维) = 5bar收益 / 3bar动量 / 5bar波动 / 量比 / 清算失衡 / 1bar收益
                 / 资金费bp / 清算笔数 / 小时 / 星期 / 区间位置 / 保留
    action = 0 做空 / 1 空仓 / 2 做多
    reward = pos × 下一根 bar 收益(bp) − |pos − 上一拍 pos| × 单边成本

    与 HFT 的本质区别:15min 级别单笔纸面交易**不推动市场** ⇒ 反事实可观测
    ("当时做多就赚下一根 bar 的涨跌幅"),reward 是**真值**不是近似。
    成本模型:仓位每变动 1 单位收 cost_per_side_bp(默认 4bp = Aster taker 单边),
    持仓不动不收费 ⇒ 教会"不折腾"(多→空 翻仓一次 8bp)。
    时间顺序切分(70/30),train/OOS 不重叠(此前同一份数据既训练又评估 = 开卷考试)。
    """

    era = "mid"

    def __init__(self, bars: Dict[str, Dict[str, np.ndarray]],
                 liqs: Dict[str, np.ndarray],
                 fund: Optional[Dict[str, np.ndarray]] = None,
                 split: float = 0.7, train: bool = True,
                 cost_per_side_bp: float = 4.0):
        self.cost = float(cost_per_side_bp)
        self._steps: List[Tuple[np.ndarray, float]] = []
        fund = fund or {}
        for sym, b in bars.items():
            c = b["c"]
            h = b.get("h", c)
            lo = b.get("l", c)
            v = b.get("v", np.ones_like(c))
            ts = b["ts"]
            liq = liqs.get(sym)
            liq_ts = liq[:, 0] if liq is not None and len(liq) else np.array([])
            liq_v = liq[:, 1] if liq is not None and len(liq) else np.array([])
            fd = fund.get(sym)
            f_ts = fd[:, 0] if fd is not None and len(fd) else np.array([])
            f_v = fd[:, 1] if fd is not None and len(fd) else np.array([])
            n = len(c)
            rows: List[Tuple[np.ndarray, float]] = []
            for i in range(5, n - 1):
                t = float(ts[i])
                m = (liq_ts > t - 15 * 60) & (liq_ts <= t)
                liq_imb = float(liq_v[m].sum()) / 1e4 if m.any() else 0.0
                liq_n = float(m.sum())
                fi = int(np.searchsorted(f_ts, t, side="right") - 1)
                fund_bp = float(f_v[fi]) * 1e4 if fi >= 0 else 0.0
                hi5 = float(np.max(h[i - 5:i]))
                lo5 = float(np.min(lo[i - 5:i]))
                range_pos = float((c[i - 1] - lo5) / (hi5 - lo5 + 1e-12))
                lt = time.localtime(t)
                obs = np.array([
                    float(c[i - 1] / c[i - 5] - 1.0) * 1e4,      # 5bar 收益
                    float(c[i - 1] / c[i - 3] - 1.0) * 1e4,      # 3bar 动量
                    float(np.std(c[i - 5:i]) / c[i - 1] * 1e4),  # 波动
                    float(v[i] / (np.mean(v[i - 5:i]) + 1e-12)),  # 量比
                    liq_imb,                                      # 清算失衡(万U)
                    float(c[i - 1] / c[i - 2] - 1.0) * 1e4,      # 1bar 收益
                    fund_bp,                                      # 资金费 bp
                    liq_n,                                        # 清算笔数
                    float(lt.tm_hour % 24), float(lt.tm_wday % 7),
                    range_pos, 0.0,
                ], np.float32)
                r_next = float((c[i] / c[i - 1] - 1.0) * 1e4)     # 下一根 bar 真值
                rows.append((obs, r_next))
            cut = int(len(rows) * split)
            self._steps.extend(rows[:cut] if train else rows[cut:])
        self._i = 0
        self._prev_pos = 0
        if not self._steps:
            self._steps = [(np.zeros(self.obs_dim, np.float32), 0.0)]

    @property
    def obs_dim(self) -> int:
        return 12

    @property
    def act_dim(self) -> int:
        return 3

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        self._i = 0
        self._prev_pos = 0
        return self._steps[0][0].astype(np.float32)

    def shape_reward(self, r_next: float, action: Any) -> tuple:
        """pos × r_next − |Δpos| × 成本。独立成方法便于单测与审计。"""
        a = int(np.argmax(action)) if np.ndim(action) > 0 else int(action)
        pos = a - 1                                   # 0→空(-1) 1→平(0) 2→多(+1)
        reward = pos * float(r_next) - abs(pos - self._prev_pos) * self.cost
        info = {"pos": pos, "prev_pos": self._prev_pos, "a": a}
        self._prev_pos = pos
        return reward, info

    def step(self, action: np.ndarray) -> tuple:
        _, r_next = self._steps[self._i]
        reward, info = self.shape_reward(r_next, action)
        self._i += 1
        if self._i >= len(self._steps):
            return (np.zeros(self.obs_dim, np.float32), reward, True,
                    {"era": self.era, **info})
        obs = self._steps[self._i][0].astype(np.float32)
        return obs, reward, False, {"era": self.era, **info}


class LongReplayEnv(HorizonEnv):
    """[h893 重写] 长线真实回放:carry_basis(持多头吃正资金费)的影子环境。

    obs(10 维) = 48bar 趋势 / 48bar 波动 / 资金费(bp/8h) / 24bar 趋势 / 量比
                 / 小时 / 星期 / 48bar 区间位置 / 6bar 动量 / 保留
    action = 仓位档 0/1/2/3(0=空仓 … 3=3 倍多)
    reward = pos × (下一根 bar 收益 + 每 bar 资金费) − |Δpos| × 单边成本

    资金费每 8h 结算一次 ⇒ 每 bar 折算 = 费率 × bar_min/480。
    与中线同理:反事实可观测,reward 是真值;调仓才收费,教会"拿稳"。
    时间顺序切分(70/30)。
    """

    era = "long"

    def __init__(self, bars: Dict[str, Dict[str, np.ndarray]],
                 fund: Dict[str, np.ndarray], bar_min: int = 15,
                 split: float = 0.7, train: bool = True,
                 cost_per_side_bp: float = 4.0):
        self.cost = float(cost_per_side_bp)
        self._fund_per_bar = float(bar_min) / 480.0     # 8h → 每 bar 折算
        self._steps: List[Tuple[np.ndarray, float]] = []
        for sym, b in bars.items():
            c = b["c"]
            h = b.get("h", c)
            lo = b.get("l", c)
            v = b.get("v", np.ones_like(c))
            ts = b["ts"]
            f = fund.get(sym)
            f_ts = f[:, 0] if f is not None and len(f) else np.array([])
            f_v = f[:, 1] if f is not None and len(f) else np.array([])
            rows: List[Tuple[np.ndarray, float]] = []
            for i in range(48, len(c) - 1):
                t = float(ts[i])
                fi = int(np.searchsorted(f_ts, t, side="right") - 1)
                fund_bp = float(f_v[fi]) * 1e4 if fi >= 0 else 0.0
                hi = float(np.max(h[i - 48:i]))
                lw = float(np.min(lo[i - 48:i]))
                lt = time.localtime(t)
                obs = np.array([
                    float(c[i - 1] / c[i - 48] - 1.0) * 1e4,     # 48bar 趋势
                    float(np.std(c[i - 48:i]) / c[i - 1] * 1e4),  # 48bar 波动
                    fund_bp,                                      # 资金费 bp/8h
                    float(c[i - 1] / c[i - 24] - 1.0) * 1e4,     # 24bar 趋势
                    float(v[i] / (np.mean(v[i - 48:i]) + 1e-12)),  # 量比
                    float(lt.tm_hour % 24), float(lt.tm_wday % 7),
                    float((c[i - 1] - lw) / (hi - lw + 1e-12)),   # 区间位置
                    float(c[i - 1] / c[i - 6] - 1.0) * 1e4,      # 6bar 动量
                    0.0,
                ], np.float32)
                r_next = (float((c[i] / c[i - 1] - 1.0) * 1e4)
                          + fund_bp * self._fund_per_bar)         # bar 收益 + 资金费
                rows.append((obs, r_next))
            cut = int(len(rows) * split)
            self._steps.extend(rows[:cut] if train else rows[cut:])
        self._i = 0
        self._prev_pos = 0
        if not self._steps:
            self._steps = [(np.zeros(self.obs_dim, np.float32), 0.0)]

    @property
    def obs_dim(self) -> int:
        return 10

    @property
    def act_dim(self) -> int:
        return 4

    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        self._i = 0
        self._prev_pos = 0
        return self._steps[0][0].astype(np.float32)

    def shape_reward(self, r_next: float, action: Any) -> tuple:
        """pos × r_next − |Δpos| × 成本。pos ∈ {0,1,2,3}。"""
        a = int(np.argmax(action)) if np.ndim(action) > 0 else int(action)
        pos = max(0, min(3, a))
        reward = pos * float(r_next) - abs(pos - self._prev_pos) * self.cost
        info = {"pos": pos, "prev_pos": self._prev_pos, "a": a}
        self._prev_pos = pos
        return reward, info

    def step(self, action: np.ndarray) -> tuple:
        _, r_next = self._steps[self._i]
        reward, info = self.shape_reward(r_next, action)
        self._i += 1
        if self._i >= len(self._steps):
            return (np.zeros(self.obs_dim, np.float32), reward, True,
                    {"era": self.era, **info})
        obs = self._steps[self._i][0].astype(np.float32)
        return obs, reward, False, {"era": self.era, **info}
