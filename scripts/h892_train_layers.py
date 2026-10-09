# -*- coding: utf-8 -*-
r"""[h892] 三层影子训练驱动:M2/M3/M4 一次跑完,输出 layers_state.json 供 M5 接入。

用法:.venv\Scripts\python.exe scripts\h892_train_layers.py [小时数]
产出:data/evolution_layers_state.json —— 三层的最新 regime/先验读数
     data/evolution_hft_oos.json —— 高频影子的 OOS 对比(基准 = 历史规则集)
"""
import io
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.evolution.layers_data import (  # noqa: E402
    load_funding, load_hft_features, load_liquidations, load_mid_bars,
)
from backend.services.evolution.unified_learner import (  # noqa: E402
    HftReplayEnv, LongReplayEnv, MidReplayEnv, TorchPPO,
    compute_regime_readings, eval_veto_thresholds, m5_veto_bp,
    promote_gate,
)

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
HFT_SYMS = ["BTCUSDT", "ETHUSDT", "NEARUSDT", "AAVEUSDT"]
MID_SYMS = ["BTC", "ETH", "SOL", "NEAR"]
LONG_SYMS = ["BTC", "ETH"]


def mean(seq):
    return float(np.mean(seq)) if len(seq) else 0.0


out = {"ts": time.time(), "layers": {}}

# ═══ M2:高频影子(真实特征回放 + OOS 对比) ═══
# [h893 2026-10-07] 三处实质升级:
#   ① ShadowLearner → TorchPPO:骨架的 train() 只收集轨迹从不更新参数
#     (策略永远停在随机初始网络),换真 PPO 后"训练"才是真的;
#   ② 环境改 action_dependent=True:reward 与动作挂钩(反事实近似,
#     见 HftReplayEnv docstring 的诚实声明),否则 RL 学不到"动作→后果";
#   ③ OOS 用 greedy 策略(部署口径)并过统一晋级门 promote_gate。
print(f"== M2 高频影子(回放 {HOURS}h 真实特征, 真 PPO)==", flush=True)
feats = load_hft_features(HFT_SYMS, hours=HOURS, step_s=1.0)
print(f"  特征装载:{ {k: len(v['obs']) for k, v in feats.items()} }", flush=True)
if feats:
    env_tr = HftReplayEnv(feats, split=0.7, train=True, action_dependent=True)
    env_te = HftReplayEnv(feats, split=0.7, train=False, action_dependent=True)
    learner = TorchPPO(env_tr, name="hft")
    hist = learner.train(steps=600, horizon=64, log_every=4)
    # OOS:训练后的策略在评估集上跑 **greedy** 动作(部署口径,不采样)
    oos_r, n = [], 0
    agree_n, size_sum = 0, 0.0
    obs = env_te.reset()
    for _ in range(min(2000, 10_000)):
        a = learner.act_greedy(obs)
        obs, r, done, info = env_te.step(a)
        oos_r.append(r)
        agree_n += int(bool(info.get("agree")))
        size_sum += float(info.get("size_mult") or 1.0)
        n += 1
        if done:
            obs = env_te.reset()
    oos_mean = mean(oos_r)
    # 基准 = 历史规则集(恒顺 OFI、名义 1 倍) ⇒ 在本塑形口径下恰等于 mean(y)
    base_mean = mean([y for _, y in env_te._steps])
    oos_hours = HOURS * 0.3
    # 统一晋级门(离线烟测口径:min_hours=0,真正晋升仍要求实盘影子小时数)
    gate_ok, gate_reason = promote_gate(
        {"n": len(env_te._steps), "mean_y": base_mean, "taker_fee_share": 0.0,
         "fill_rate": 1.0, "liquidations": 0, "hours": oos_hours},
        {"n": n, "mean_y": oos_mean, "taker_fee_share": 0.0,
         "fill_rate": 1.0, "liquidations": 0, "hours": oos_hours},
        min_hours=0.0)
    out["layers"]["hft"] = {
        "train_ep_mean": mean(hist[-4:]), "oos_mean_y": round(oos_mean, 3),
        "baseline_mean_y": round(base_mean, 3), "oos_n": n,
        "verdict": "learned≥baseline" if oos_mean >= base_mean else "learned<baseline",
        "learner": "TorchPPO", "action_dependent": True,
        "greedy_agree_ofi": round(agree_n / max(1, n), 3),
        "greedy_size_mean": round(size_sum / max(1, n), 3),
        "train_stats": learner.last_train_stats,
        "gate_offline_smoke": {"ok": gate_ok, "reason": gate_reason},
    }
    # docstring 承诺的 OOS 对比文件(此前只写了 layers_state,没写这份)
    (ROOT / "data" / "evolution_hft_oos.json").write_text(json.dumps(
        {"ts": out["ts"], "hours": HOURS, "oos_hours": round(oos_hours, 2),
         "symbols": HFT_SYMS, **out["layers"]["hft"]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  OOS:影子均值 {oos_mean:+.3f}bp vs 基准 {base_mean:+.3f}bp "
          f"(n={n}) → {out['layers']['hft']['verdict']} | "
          f"顺OFI率 {out['layers']['hft']['greedy_agree_ofi']:.0%} "
          f"均名义档 {out['layers']['hft']['greedy_size_mean']:.2f} | "
          f"晋级门(离线烟测): {gate_reason}", flush=True)
    # [h899 A] 影子车道闭环:导出学到的策略(纯 Python 可加载)+ 反事实评估
    try:
        from backend.services.evolution.shadow_feedback import (
            evaluate_shadow, export_torch_actor)
        export_torch_actor(learner._actor, env_tr.act_dim)
        print("  ✓ 策略已导出 evolution_policy.json", flush=True)
        ev = evaluate_shadow(hours=max(2.0, HOURS))
        out["layers"]["hft"]["shadow_eval"] = ev
        print(f"  影子反事实: {json.dumps(ev, ensure_ascii=False)}", flush=True)
    except Exception as e:
        print(f"  ⚠ 影子评估跳过: {e}", flush=True)
else:
    out["layers"]["hft"] = {"error": "无足够特征"}
    print("  ⚠ 无足够特征,跳过", flush=True)

# ═══ M3:中线影子(15min bar + 清算;真 reward + OOS) ═══
# [h893] 中线环境已重写:reward = pos × 下一根 bar 真值收益 − 调仓成本
# (15min 纸面交易不推动市场 ⇒ 反事实可观测,reward 是真值不是近似);
# 基准 = 清算反转规则(|liq_imb|>0.5 ⇒ 逆侧持仓否则空仓,同一成本模型)。
# 符号约定假设:liq_imb>0 = 多头被爆(价格下跌) ⇒ 反转=做多;RL 本身不依赖
# 该约定(它直接从 obs→action→下根bar真值 学习),只有基准规则用。
print("== M3 中线影子(真 PPO + 动作相关 reward)==", flush=True)
bars_m = load_mid_bars(MID_SYMS, days=2.0, bar_min=15)
liqs = load_liquidations(MID_SYMS, hours=48)
fund_m = load_funding(MID_SYMS, hours=48)
print(f"  bar:{ {k: len(v['c']) for k, v in bars_m.items()} } "
      f"清算:{ {k: len(v) for k, v in liqs.items()} }", flush=True)
if bars_m:
    env_mtr = MidReplayEnv(bars_m, liqs, fund_m, split=0.7, train=True)
    env_mte = MidReplayEnv(bars_m, liqs, fund_m, split=0.7, train=False)
    lm = TorchPPO(env_mtr, name="mid")
    hm = lm.train(steps=1200, horizon=96, log_every=4)
    # OOS:greedy 策略走完整评估集(时间顺序)
    obs = env_mte.reset()
    oos_r, flat_n, n = [], 0, 0
    while True:
        a = lm.act_greedy(obs)
        obs, r, done, info = env_mte.step(a)
        oos_r.append(r)
        flat_n += int(info.get("pos") == 0)
        n += 1
        if done:
            break
    oos_mean = mean(oos_r)
    # 基准:清算反转规则(与影子同一成本模型、同一 OOS 段)
    base_r, prev_b = [], 0
    for obs_s, r_next in env_mte._steps:
        liq_imb = float(obs_s[4])
        pos_b = 1 if liq_imb > 0.5 else (-1 if liq_imb < -0.5 else 0)
        base_r.append(pos_b * r_next - abs(pos_b - prev_b) * env_mte.cost)
        prev_b = pos_b
    base_mean = mean(base_r)
    gate_ok, gate_reason = promote_gate(
        {"n": len(base_r), "mean_y": base_mean, "taker_fee_share": 1.0,
         "fill_rate": 1.0, "liquidations": 0, "hours": 2.0 * 0.3 * 24},
        {"n": n, "mean_y": oos_mean, "taker_fee_share": 1.0,
         "fill_rate": 1.0, "liquidations": 0, "hours": 2.0 * 0.3 * 24},
        min_hours=0.0)
    out["layers"]["mid"] = {
        "train_ep_mean": round(mean(hm[-4:]), 3), "oos_mean_y": round(oos_mean, 3),
        "baseline_mean_y": round(base_mean, 3), "oos_n": n,
        "verdict": "learned≥baseline" if oos_mean >= base_mean else "learned<baseline",
        "learner": "TorchPPO", "action_dependent": True,
        "greedy_flat_rate": round(flat_n / max(1, n), 3),
        "train_stats": lm.last_train_stats,
        "gate_offline_smoke": {"ok": gate_ok, "reason": gate_reason},
    }
    print(f"  OOS:影子 {oos_mean:+.3f}bp vs 清算反转基准 {base_mean:+.3f}bp "
          f"(n={n}) → {out['layers']['mid']['verdict']} | "
          f"空仓率 {out['layers']['mid']['greedy_flat_rate']:.0%} | "
          f"晋级门: {gate_reason}", flush=True)
else:
    out["layers"]["mid"] = {"error": "无足够 bar"}
    print("  ⚠ 无足够 bar,跳过", flush=True)

# ═══ M4:长线影子(日级 + 资金费;真 reward + OOS) ═══
# [h893] 长线环境已重写:reward = 仓位档 × (bar 收益 + 每 bar 资金费折算)
# − 调仓成本;基准 = 恒 1 倍多头 carry(不动仓 ⇒ 几乎零成本)。
# 资金费窗口对齐 bar 窗口(7 天),此前只取 48h ⇒ 旧 bar 资金费全 0。
print("== M4 长线影子(真 PPO + 动作相关 reward)==", flush=True)
bars_l = load_mid_bars(LONG_SYMS, days=7.0, bar_min=15)
fund = load_funding(LONG_SYMS, hours=7 * 24 + 8)
print(f"  bar:{ {k: len(v['c']) for k, v in bars_l.items()} } "
      f"资金费:{ {k: len(v) for k, v in fund.items()} }", flush=True)
if bars_l:
    env_ltr = LongReplayEnv(bars_l, fund, split=0.7, train=True)
    env_lte = LongReplayEnv(bars_l, fund, split=0.7, train=False)
    ll = TorchPPO(env_ltr, name="long")
    hl = ll.train(steps=1200, horizon=96, log_every=4)
    obs = env_lte.reset()
    oos_r, pos_sum, n = [], 0.0, 0
    while True:
        a = ll.act_greedy(obs)
        obs, r, done, info = env_lte.step(a)
        oos_r.append(r)
        pos_sum += float(info.get("pos") or 0)
        n += 1
        if done:
            break
    oos_mean = mean(oos_r)
    # 基准:恒 1 倍多头 carry(持仓不动 ⇒ 仅首步一次成本)
    base_r, prev_b = [], 0
    for obs_s, r_next in env_lte._steps:
        base_r.append(1 * r_next - abs(1 - prev_b) * env_lte.cost)
        prev_b = 1
    base_mean = mean(base_r)
    gate_ok, gate_reason = promote_gate(
        {"n": len(base_r), "mean_y": base_mean, "taker_fee_share": 1.0,
         "fill_rate": 1.0, "liquidations": 0, "hours": 7.0 * 0.3 * 24},
        {"n": n, "mean_y": oos_mean, "taker_fee_share": 1.0,
         "fill_rate": 1.0, "liquidations": 0, "hours": 7.0 * 0.3 * 24},
        min_hours=0.0)
    out["layers"]["long"] = {
        "train_ep_mean": round(mean(hl[-4:]), 3), "oos_mean_y": round(oos_mean, 3),
        "baseline_mean_y": round(base_mean, 3), "oos_n": n,
        "verdict": "learned≥baseline" if oos_mean >= base_mean else "learned<baseline",
        "learner": "TorchPPO", "action_dependent": True,
        "greedy_pos_mean": round(pos_sum / max(1, n), 3),
        "train_stats": ll.last_train_stats,
        "gate_offline_smoke": {"ok": gate_ok, "reason": gate_reason},
    }
    print(f"  OOS:影子 {oos_mean:+.3f}bp vs 恒1倍carry基准 {base_mean:+.3f}bp "
          f"(n={n}) → {out['layers']['long']['verdict']} | "
          f"均仓位 {out['layers']['long']['greedy_pos_mean']:.2f}x | "
          f"晋级门: {gate_reason}", flush=True)
else:
    out["layers"]["long"] = {"error": "无足够 bar"}
    print("  ⚠ 无足够 bar,跳过", flush=True)

# ═══ M5:三层互联(模拟仓直连 + 效果监测) ═══
# [h893c] 长线 regime → 中线先验 → 高频 10min 趋势否决阈值(m5_veto_bp,
# 与 runner.py 模拟仓 worker **同一函数** ⇒ 口径一致)。本段写出的
# layers.m5 读数**直接被模拟仓 worker 每拍读取生效**(纸面,零资金风险);
# eval_veto_thresholds 是**监测仪器**:在历史数据上对比 动态 vs 静态基线
# 各拦了什么、拦得对不对 —— 观测用,不是旁路影子层。
print("== M5 三层互联(模拟仓直连 + 监测)==", flush=True)
if feats and bars_l and bars_m:
    long_state, mid_state = compute_regime_readings(bars_l, bars_m)
    dyn_veto = m5_veto_bp(float(long_state[0]), float(mid_state[0]), base_bp=4.0)
    veto_cmp = eval_veto_thresholds(env_te._steps, 4.0, dyn_veto)
    out["layers"]["m5"] = {
        "long_regime_bp": round(float(long_state[0]), 2),
        "mid_prior_bp": round(float(mid_state[0]), 2),
        "veto_base_bp": 4.0,
        "veto_dynamic_bp": round(float(dyn_veto), 2),
        "veto_compare": veto_cmp,
        "note": "模拟仓直连:worker 每拍读本节长/中线读数算否决阈值;"
                "veto_compare 为监测(历史回放对比),不影响交易",
    }
    _d = veto_cmp.get("dyn_only") or {}
    print(f"  长线 regime {float(long_state[0]):+.1f}bp / 中线先验 "
          f"{float(mid_state[0]):+.1f}bp ⇒ 模拟仓否决阈值 {float(dyn_veto):.1f}bp "
          f"(基线 4.0bp)", flush=True)
    print(f"  监测:动态多拦 {_d.get('n', 0)} 笔,平均 y = {_d.get('mean_y')}bp "
          f"(为负 = 拦得对)", flush=True)
else:
    out["layers"]["m5"] = {"error": "三层数据不齐,跳过"}
    print("  ⚠ 三层数据不齐,跳过", flush=True)

# ═══ 写出 layers_state.json(M5 用) ═══
p = ROOT / "data" / "evolution_layers_state.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✓ 已写 {p.name}", flush=True)
