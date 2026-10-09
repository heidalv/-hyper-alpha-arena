# -*- coding: utf-8 -*-
"""[h893 2026-10-07] TorchPPO + HftReplayEnv(action_dependent) + promote_gate 单测。

核心断言:**TorchPPO.train() 真的在更新参数**(numpy 骨架只收集轨迹不更新,
策略永远停在随机初始网络 —— 本文件的 bandit 测试就是防这个回归)。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.evolution.unified_learner import (
    HorizonEnv,
    HftReplayEnv,
    LongReplayEnv,
    MidReplayEnv,
    TorchPPO,
    compute_regime_readings,
    cross_horizon_prior,
    eval_veto_thresholds,
    m5_veto_bp,
    promote_gate,
)


# ──────────────────────────────────────────────────────────────────────
# 合成环境:正确答案依赖 obs 符号(可学习结构)
# ──────────────────────────────────────────────────────────────────────
class _BanditEnv(HorizonEnv):
    """obs[0] > 0 ⇒ 动作 0 得 +1 否则 -1;obs[0] <= 0 ⇒ 动作 1 得 +1 否则 -1。

    线性可分的上下文赌臂:一个真会学习的 PPO 必须能把平均 reward 从 ~0 拉到 >0。
    """

    era = "test"

    def __init__(self, horizon: int = 64):
        self._rng = np.random.default_rng(7)
        self._obs = np.zeros(2, np.float32)
        self._horizon = horizon
        self._i = 0

    @property
    def obs_dim(self) -> int:
        return 2

    @property
    def act_dim(self) -> int:
        return 2

    def reset(self, seed=None):
        self._i = 0
        self._obs = self._rng.standard_normal(2).astype(np.float32)
        return self._obs

    def step(self, action):
        a = int(np.argmax(action)) if np.ndim(action) > 0 else int(action)
        correct = 0 if self._obs[0] > 0 else 1
        r = 1.0 if a == correct else -1.0
        self._i += 1
        done = self._i >= self._horizon
        self._obs = self._rng.standard_normal(2).astype(np.float32)
        return self._obs, r, done, {"era": self.era}


# ──────────────────────────────────────────────────────────────────────
# 1. 接口兼容(与 ShadowLearner 同口径,可即插替换)
# ──────────────────────────────────────────────────────────────────────
def test_torch_ppo_interface():
    env = _BanditEnv()
    ppo = TorchPPO(env, name="t")
    obs = env.reset()
    onehot, logp = ppo.policy(obs)
    assert onehot.shape == (env.act_dim,)
    assert float(onehot.sum()) == pytest.approx(1.0)
    assert isinstance(logp, float) and logp <= 0.0
    a = ppo.act_greedy(obs)
    assert 0 <= a < env.act_dim


# ──────────────────────────────────────────────────────────────────────
# 2. 核心:train() 必须真的让策略变好(防"假训练"回归)
# ──────────────────────────────────────────────────────────────────────
def test_torch_ppo_actually_learns():
    env = _BanditEnv(horizon=64)
    ppo = TorchPPO(env, name="learn", seed=1, epochs=4)
    hist = ppo.train(steps=64 * 40, horizon=64, log_every=0)
    assert len(hist) == 40
    head = float(np.mean(hist[:5]))
    tail = float(np.mean(hist[-5:]))
    # 随机策略在该环境期望 reward ≈ 0;学会后应显著为正
    assert tail > head, f"训练没有改进: head={head:+.3f} tail={tail:+.3f}"
    assert tail > 0.3, f"没学会可学习结构: tail={tail:+.3f}"
    # greedy 策略应接近满分
    correct = 0
    obs = env.reset()
    for _ in range(200):
        a = ppo.act_greedy(obs)
        obs, r, done, _ = env.step(np.eye(2, dtype=np.float32)[a])
        correct += int(r > 0)
        if done:
            obs = env.reset()
    assert correct / 200 > 0.85, f"greedy 命中率过低: {correct / 200:.2%}"


# ──────────────────────────────────────────────────────────────────────
# 3. 反事实奖励塑形的数学(逐档核对)
# ──────────────────────────────────────────────────────────────────────
def _mk_feats():
    obs = np.zeros((4, 15), np.float32)
    obs[:, 1] = [1.0, 1.0, -1.0, 0.0]          # ofi_5s: 正 / 正 / 负 / 零
    y = np.array([10.0, -4.0, 8.0, 5.0], np.float32)
    return {"TEST": {"obs": obs, "y": y}}


def test_action_dependent_reward_shaping():
    env = HftReplayEnv(_mk_feats(), split=1.0, train=True,
                       action_dependent=True, cf_discount=0.5)
    obs = env._steps
    # 动作编码:a = side*9 + price_slot*3 + size_slot;size_mult=(0.5,1,2)
    # ① ofi>0 选多(side=0) ⇒ 顺势:10 × 0.5 = 5
    r, info = env.shape_reward(obs[0][0], 10.0, 0)
    assert r == pytest.approx(5.0) and info["agree"] is True
    # ② ofi>0 选空(side=1) ⇒ 反势:-0.5 × (-4) × 0.5 = +1(坏顺向单 ⇒ 反向有奖)
    r, info = env.shape_reward(obs[1][0], -4.0, 9)
    assert r == pytest.approx(1.0) and info["agree"] is False
    # ③ ofi<0 选空(side=1, size_slot=2 ⇒ 2 倍) ⇒ 顺势放大:8 × 2 = 16
    r, info = env.shape_reward(obs[2][0], 8.0, 9 + 2)
    assert r == pytest.approx(16.0) and info["size_mult"] == 2.0
    # ④ ofi=0(无方向) ⇒ 视为顺势:5 × 2 = 10
    r, info = env.shape_reward(obs[3][0], 5.0, 2)
    assert r == pytest.approx(10.0) and info["agree"] is True


def test_action_independent_mode_unchanged():
    """默认模式(action_dependent=False)保持旧行为:reward 恒等于历史 y。"""
    env = HftReplayEnv(_mk_feats(), split=1.0, train=True)
    env.reset()
    for i in range(3):
        _, r, _, _ = env.step(np.zeros(18, np.float32))
        assert r == pytest.approx(float([10.0, -4.0, 8.0][i]))


# ──────────────────────────────────────────────────────────────────────
# 4. 统一晋级门各分支
# ──────────────────────────────────────────────────────────────────────
def _base():
    return {"n": 100, "mean_y": 10.0, "taker_fee_share": 0.10,
            "fill_rate": 0.80, "liquidations": 0, "hours": 3.0}


def test_promote_gate_pass():
    after = dict(_base())
    after["mean_y"] = 9.0                       # ≥ 基准 80%
    ok, reason = promote_gate(_base(), after, min_hours=2.0)
    assert ok and reason == "promote"


def test_promote_gate_rejects():
    base = _base()
    # 样本不足
    ok, _ = promote_gate(base, {**_base(), "n": 10}, min_hours=2.0)
    assert not ok
    # 平均 y 跌破基准 80%(基准为正)
    ok, r = promote_gate(base, {**_base(), "mean_y": 7.0}, min_hours=2.0)
    assert not ok and "基准" in r
    # 吃单费占比上升
    ok, _ = promote_gate(base, {**_base(), "taker_fee_share": 0.20}, min_hours=2.0)
    assert not ok
    # 挂单率塌
    ok, _ = promote_gate(base, {**_base(), "fill_rate": 0.50}, min_hours=2.0)
    assert not ok
    # 强平 > 0
    ok, _ = promote_gate(base, {**_base(), "liquidations": 1}, min_hours=2.0)
    assert not ok
    # OOS 小时数不足
    ok, _ = promote_gate(base, {**_base(), "hours": 1.0}, min_hours=2.0)
    assert not ok


def test_promote_gate_negative_baseline():
    """[h893b] 基准为负时的边界(旧逻辑在此完全漏检):
    影子不得比负基准再差 20%|基准|;略好于基准则放行。"""
    neg = {**_base(), "mean_y": -0.65}          # 长线实测:恒 1 倍 carry 也在亏
    # 影子 −1.76 比基准再差 1.11bp ≫ 容忍 0.13 ⇒ 必须拒
    ok, r = promote_gate(neg, {**_base(), "mean_y": -1.76}, min_hours=2.0)
    assert not ok and "基准" in r
    # 影子 −0.60 略好于基准 ⇒ 放行
    ok, _ = promote_gate(neg, {**_base(), "mean_y": -0.60}, min_hours=2.0)
    assert ok
    # 基准≈0 时由 0.05bp 地板兜底:影子 −0.06 也要拒
    zero = {**_base(), "mean_y": 0.001}
    ok, _ = promote_gate(zero, {**_base(), "mean_y": -0.06}, min_hours=2.0)
    assert not ok


# ──────────────────────────────────────────────────────────────────────
# 5. 时间顺序切分:train/OOS 不重叠(防未来数据泄漏)
# ──────────────────────────────────────────────────────────────────────
def test_hft_replay_env_time_split_disjoint():
    obs = np.arange(10 * 15, dtype=np.float32).reshape(10, 15)
    y = np.arange(10, dtype=np.float32)
    feats = {"T": {"obs": obs, "y": y}}
    tr = HftReplayEnv(feats, split=0.7, train=True)
    te = HftReplayEnv(feats, split=0.7, train=False)
    assert len(tr._steps) == 7 and len(te._steps) == 3
    # 训练集最后一条的 y 必须早于评估集第一条(时间顺序,不洗牌)
    assert tr._steps[-1][1] < te._steps[0][1]


# ──────────────────────────────────────────────────────────────────────
# 6. 中线环境(h893 重写):动作相关 reward + 调仓成本 + 时间切分
# ──────────────────────────────────────────────────────────────────────
def _mk_mid_bars(n: int = 40):
    """构造 40 根 15min bar:前 20 根上涨、后 20 根下跌(可学习结构)。"""
    ts = 1_700_000_000 + 900 * np.arange(n)
    c = np.ones(n) * 100.0
    for i in range(1, n):
        c[i] = c[i - 1] * (1.001 if i < 20 else 0.999)   # 每根 ±10bp
    return {"T": {"ts": ts.astype(float), "o": c.copy(), "h": c * 1.001,
                  "l": c * 0.999, "c": c, "v": np.ones(n) * 100.0,
                  "amt": np.ones(n) * 10000.0}}


def test_mid_env_action_dependent_reward():
    env = MidReplayEnv(_mk_mid_bars(), liqs={}, split=1.0, train=True,
                       cost_per_side_bp=4.0)
    # r_next 在上涨段恒为 +10bp(c[i]/c[i-1] = 1.001)
    env.reset()
    r, info = env.shape_reward(10.0, 2)      # 做多:10 − |1−0|×4 = 6
    assert r == pytest.approx(6.0) and info["pos"] == 1
    r, info = env.shape_reward(10.0, 2)      # 继续做多:10 − 0 = 10(不动不收费)
    assert r == pytest.approx(10.0)
    r, info = env.shape_reward(10.0, 0)      # 翻空:−10 − |−1−1|×4 = −18
    assert r == pytest.approx(-18.0) and info["pos"] == -1
    r, info = env.shape_reward(10.0, 1)      # 空仓:0 − |0−(−1)|×4 = −4
    assert r == pytest.approx(-4.0) and info["pos"] == 0
    r, info = env.shape_reward(10.0, 1)      # 持续空仓:0 − 0 = 0
    assert r == pytest.approx(0.0)


def test_mid_env_split_disjoint():
    bars = _mk_mid_bars(40)
    tr = MidReplayEnv(bars, {}, split=0.7, train=True)
    te = MidReplayEnv(bars, {}, split=0.7, train=False)
    # 每币可用行 = 40 − 5(特征窗) − 1(下一根) = 34;70/30 切分
    assert len(tr._steps) == 23 and len(te._steps) == 11
    assert len(tr._steps) + len(te._steps) == 34


def test_mid_env_reset_clears_position():
    env = MidReplayEnv(_mk_mid_bars(), {}, split=1.0, train=True)
    env.reset()
    env.shape_reward(10.0, 2)                # 持仓 +1
    env.reset()                              # reset 必须清回 0
    assert env._prev_pos == 0


# ──────────────────────────────────────────────────────────────────────
# 7. 长线环境(h893 重写):仓位档 + 资金费折算 + 调仓成本
# ──────────────────────────────────────────────────────────────────────
def _mk_long_bars(n: int = 60, fund_rate: float = 0.0001):
    ts = 1_700_000_000 + 900 * np.arange(n)
    c = 100.0 * np.ones(n)
    for i in range(1, n):
        c[i] = c[i - 1] * 1.0005             # 每根 +5bp
    bars = {"T": {"ts": ts.astype(float), "o": c.copy(), "h": c * 1.001,
                  "l": c * 0.999, "c": c, "v": np.ones(n) * 50.0,
                  "amt": np.ones(n) * 5000.0}}
    fund = {"T": np.array([(float(ts[0]) - 10.0, fund_rate)])}  # 一条足够
    return bars, fund


def test_long_env_reward_with_funding():
    bars, fund = _mk_long_bars()
    env = LongReplayEnv(bars, fund, bar_min=15, split=1.0, train=True,
                        cost_per_side_bp=4.0)
    env.reset()
    # r_next = 5bp + 资金费折算(0.0001 × 1e4 × 15/480 = 0.03125bp)
    r_next = 5.0 + 0.0001 * 1e4 * (15 / 480)
    r, info = env.shape_reward(r_next, 3)    # 3 倍建仓:3×r − |3−0|×4
    assert r == pytest.approx(3 * r_next - 12.0) and info["pos"] == 3
    r, info = env.shape_reward(r_next, 1)    # 降到 1 倍:1×r − |1−3|×4
    assert r == pytest.approx(r_next - 8.0)
    r, info = env.shape_reward(r_next, 1)    # 拿稳不动:1×r − 0
    assert r == pytest.approx(r_next)
    r, info = env.shape_reward(r_next, 0)    # 清仓:0 − |0−1|×4
    assert r == pytest.approx(-4.0)


def test_long_env_split_and_funding_window():
    bars, fund = _mk_long_bars(60)
    tr = LongReplayEnv(bars, fund, split=0.7, train=True)
    te = LongReplayEnv(bars, fund, split=0.7, train=False)
    # 可用行 = 60 − 48(特征窗) − 1(下一根) = 11;70/30 → 7 / 4
    assert len(tr._steps) == 7 and len(te._steps) == 4
    # 资金费必须进了 r_next(5bp 涨幅 + 0.03125bp 资金费)
    assert tr._steps[0][1] == pytest.approx(5.0 + 0.03125, abs=1e-6)


# ──────────────────────────────────────────────────────────────────────
# 8. M5 三层互联:regime 读数 / 先验阈值 / 否决对比
# ──────────────────────────────────────────────────────────────────────
def test_compute_regime_readings():
    # 长线:50 根单调上涨(每根 +1%) ⇒ 48bar 趋势 ≈ +10000×(1.01^48−1)bp
    n = 50
    c_up = 100.0 * np.cumprod([1.0] + [1.01] * (n - 1))
    c_dn = 100.0 * np.cumprod([1.0] + [0.99] * (n - 1))
    bars_l = {"UP": {"c": c_up, "ts": np.arange(n) * 900.0},
              "DN": {"c": c_dn, "ts": np.arange(n) * 900.0}}
    bars_m = {"M": {"c": c_up, "ts": np.arange(n) * 900.0}}
    long_state, mid_state = compute_regime_readings(bars_l, bars_m)
    # 一涨一跌均值 ≈ 0 附近,但涨跌幅不对称 ⇒ 略正;符号不重要,量级要在
    assert abs(float(long_state[0])) < 3000.0
    # 中线只有上涨币:c[-2]/c[-6] 跨 4 个间隔 ⇒ +10000×(1.01^4−1) ≈ +406bp
    assert float(mid_state[0]) == pytest.approx(406.04, abs=2.0)
    # 空数据 ⇒ 0(不崩)
    ls2, ms2 = compute_regime_readings({}, {})
    assert float(ls2[0]) == 0.0 and float(ms2[0]) == 0.0


def test_cross_horizon_prior_logic():
    # 长线偏空 ⇒ 阈值收紧(×0.7)
    v = cross_horizon_prior(np.array([-100.0]), np.array([50.0]), None,
                            base_veto_bp=8.0)
    assert v == pytest.approx(5.6)
    # 中线无方向 ⇒ 阈值放宽(×1.3)
    v = cross_horizon_prior(np.array([100.0]), np.array([0.05]), None,
                            base_veto_bp=8.0)
    assert v == pytest.approx(10.4)
    # 两者同时:8 × 0.7 × 1.3 = 7.28
    v = cross_horizon_prior(np.array([-100.0]), np.array([0.0]), None,
                            base_veto_bp=8.0)
    assert v == pytest.approx(7.28)
    # 都中性 ⇒ 基线不变;下限 1bp
    v = cross_horizon_prior(np.array([100.0]), np.array([50.0]), None,
                            base_veto_bp=8.0)
    assert v == pytest.approx(8.0)
    v = cross_horizon_prior(np.array([-1.0]), None, None, base_veto_bp=1.0)
    assert v == pytest.approx(1.0)


def test_eval_veto_thresholds():
    # 构造 6 拍:ofi(idx1) / trend_300s(idx4) / y 已知
    def _obs(ofi, trend):
        o = np.zeros(15, np.float32)
        o[1], o[4] = ofi, trend
        return o
    steps = [
        (_obs(+1.0, -20.0), -5.0),   # 逆势(多 vs 跌),|trend|=20 ⇒ 静态/动态都拦
        (_obs(+1.0, -10.0), -3.0),   # 逆势,10bp ⇒ 静态(8)拦、动态(12)放
        (_obs(+1.0, -15.0), +2.0),   # 逆势但被拦的对像是赚的(误拦样本)
        (_obs(+1.0, +20.0), +1.0),   # 顺势 ⇒ 不拦
        (_obs(-1.0, +20.0), -4.0),   # 逆势(空 vs 涨) ⇒ 都拦
        (_obs(0.0, -30.0), -9.0),    # ofi=0 ⇒ 跳过
    ]
    out = eval_veto_thresholds(steps, static_bp=8.0, dyn_bp=12.0)
    assert out["static"]["n"] == 4          # 逆势四笔:20/10/15bp + 空vs涨20bp
    assert out["dynamic"]["n"] == 3         # 20/15/20bp(10bp 被放行)
    assert out["static_only"]["n"] == 1     # 10bp 那笔
    assert out["static_only"]["mean_y"] == pytest.approx(-3.0)
    assert out["dyn_only"]["n"] == 0        # 动态更宽 ⇒ 没有多拦
    assert out["static"]["mean_y"] == pytest.approx((-5.0 - 3.0 + 2.0 - 4.0) / 4)


def test_m5_veto_bp_worker_parity():
    """[h893c] 模拟仓直连阈值(runner.py 与本函数同口径):
    · 长线有方向(|regime|≥20bp) ⇒ ×0.7 收紧(双向对称)
    · 中线无方向(|prior|<10bp) ⇒ ×1.3 放宽
    · 读数缺失 ⇒ 基线 4.0(fail-closed);输出夹在 [1, 8]"""
    # 基线:读数都中性
    assert m5_veto_bp(0.0, 50.0) == pytest.approx(4.0)
    # 长线偏空(−25bp) ⇒ 4×0.7 = 2.8
    assert m5_veto_bp(-25.0, 50.0) == pytest.approx(2.8)
    # 长线偏多(+25bp) ⇒ 同样收紧(双向)
    assert m5_veto_bp(25.0, 50.0) == pytest.approx(2.8)
    # 中线无方向 ⇒ 4×1.3 = 5.2
    assert m5_veto_bp(0.0, 5.0) == pytest.approx(5.2)
    # 两者叠加:4×0.7×1.3 = 3.64
    assert m5_veto_bp(-30.0, 0.0) == pytest.approx(3.64)
    # 缺失读数 ⇒ 基线
    assert m5_veto_bp(None, None) == pytest.approx(4.0)
    # 上限夹紧:base 8 + 中线无方向 ⇒ min(8, 10.4) = 8
    assert m5_veto_bp(0.0, 0.0, base_bp=8.0) == pytest.approx(8.0)
    # 下限夹紧:base 1 + 长线有方向 ⇒ max(1, 0.7) = 1
    assert m5_veto_bp(-100.0, 100.0, base_bp=1.0) == pytest.approx(1.0)
