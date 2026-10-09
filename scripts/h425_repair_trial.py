# -*- coding: utf-8 -*-
"""H425/H426/H427 出场架构修复试跑（用户 2026-09-28 "全面修复"委任，根因审计四项之二/三/四）。

T2 h425: jump_exit_bp 0→12 —— 激活 F348 跳变速退（单 tick ≥12bp 直接 taker 离场）。
         审计依据：止损尾部 −80~−130bp（40bp 线被 15s tick 打穿），F348 设计即以
         12bp 速退封顶跳变损失（"等 40bp 兜底接住时已 −33bp"）。
T3 h426: stop_loss_vol_min 0→1.0 —— 激活 F231 波动条件止损（σ_norm≥1.0 才武装）。
         审计依据：F230/F231 设计实证"常数止损在正常日被震荡反复打、白付 taker 费，
         只在高波动 regime 划算"，而参数从未激活（=0 恒启用，设计意图被反转）。
T4 h427: max_one_side_seconds 90→45 —— 薄盘加速出库（软超时提前，停止加仓更早）。
         审计依据：12h 止损 30 笔全在 7 薄盘新币（被动出场失败率高），BTC/ETH/BNB
         零笔——同一 90s 软超时对厚薄盘不该一样；45s 让薄盘仓在深度还够时收窄。

部署守卫（出场侧修复链互斥 + 稳定基线）：任一 SERIAL_KEYS 试跑进行中 ⇒ 拒绝；
h411（#19 硬上限）必须已终判（PASS/ROLLBACK）才能叠加；--force 绕过并记审计。
判定（T+12h）：A legs/h≥0.8×基线；B 逐腿 net_bp Welch α=0.10；C 子口径必报。
回滚：--rollback 热采用（纯参数 ≤60s，无需重启）。

用法:
  python scripts/h425_repair_trial.py --trial h425 --deploy [--force]
  python scripts/h425_repair_trial.py --trial h425 --rollback
  python scripts/h425_repair_trial.py --trial h425 --judge [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
ALPHA = 0.10
FREQ_FLOOR = 0.8
BASELINE_HOURS = 12.0
MIN_JUDGE_HOURS = 10.0
EXTEND_HOURS = 13.0
# 出场侧修复链（含既有链上试跑）——任一进行中都拒绝部署，保证单变量串行
SERIAL_KEYS = ["h389_trial", "h357_trial", "h411_trial", "h399_trial",
               "h392_trial", "h425_trial", "h426_trial", "h427_trial", "h429_trial",
               "h432_trial", "h433_trial", "h434_trial", "h435_trial",
               "h436_trial", "h437_trial", "h438_trial", "h439_trial", "h440_trial",
               "h441_trial", "h442_trial", "h443_trial", "h448_trial", "h452_trial",
               "h454_trial",
               # [R203 2026-09-29] **把②自己加进来**：`h463_trial` 跑在窗口里时，
               # 任何非 `--force` 的部署都应被拒 —— 此前它**不在**这个名单里 ⇒
               # 手滑部署就会落进 ② 的窗口、把今天刚查清的对比弄脏 ✗
               # （链走 `--force` 会绕过，那是既有设计；这条守的是**人**和**脚本** ✓）
               "h463_trial"]

SPECS = {
    "h425": {
        "field": "params.jump_exit_bp", "to": 12.0, "rollback_to": 0.0,
        "meta_key": "h425_trial", "task": "DSH_HFT_H425_JUDGE",
        "title": "T2 跳变速退激活（F348 jump_exit_bp 0→12）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 止损尾部收窄（max|net| 与"
                    "−80bp 外笔数）+ jump_exit 腿数/均值",
    },
    "h426": {
        "field": "params.stop_loss_vol_min", "to": 1.0, "rollback_to": 0.0,
        "meta_key": "h426_trial", "task": "DSH_HFT_H426_JUDGE",
        "title": "T3 波动条件止损激活（F231 stop_loss_vol_min 0→1.0）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 平静段止损笔数下降、"
                    "止损腿均值不变差",
    },
    "h427": {
        "field": "params.max_one_side_seconds", "to": 45.0, "rollback_to": 90.0,
        "meta_key": "h427_trial", "task": "DSH_HFT_H427_JUDGE",
        "title": "T4 薄盘加速出库（max_one_side_seconds 90→45）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 硬上限笔数下降、"
                    "持仓时长分布左移",
    },
    "h429": {
        "field": "params.sudden_move_cooldown_sec", "to": 90.0, "rollback_to": 0.0,
        "meta_key": "h429_trial", "task": "DSH_HFT_H429_JUDGE",
        "title": "T5 急动冷却激活（sudden_move_cooldown_sec 0→90）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 止损笔数下降、"
                    "sudden_move 腿均值不恶化",
    },
    "h432": {
        "field": "params.exit_skew_k", "to": 1.0, "rollback_to": 0.0,
        "meta_key": "h432_trial", "task": "DSH_HFT_H432_JUDGE",
        "title": "v2 出场架构（exit_skew_k 0→1.0 + vol_spread_k 0→0.5）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 强平笔数下降、"
                    "taker 费占比下降",
        "fields": [("exit_skew_k", 1.0, 0.0), ("vol_spread_k", 0.5, 0.0)],
    },
    "h433": {
        "field": "params.vwap_flow_block", "to": 0.3, "rollback_to": 0.0,
        "meta_key": "h433_trial", "task": "DSH_HFT_H433_JUDGE",
        "title": "P2 流向闸通电（vwap_flow_block 0→0.3，h359）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C vwap_revert 组亏损收窄",
    },
    "h434": {
        "field": "params.pullback_flow_block", "to": 0.3, "rollback_to": 0.0,
        "meta_key": "h434_trial", "task": "DSH_HFT_H434_JUDGE",
        "title": "回调流向闸通电（pullback_flow_block 0→0.3，h357）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 回调 fade 亏损收窄",
    },
    "h435": {
        "field": "params.trail_lock_bp", "to": 20.0, "rollback_to": 0.0,
        "meta_key": "h435_trial", "task": "DSH_HFT_H435_JUDGE",
        "title": "#17 尾随锁利通电（trail_lock_bp 0→20，h389）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 浮盈回吐收窄",
    },
    "h436": {
        "field": "params.post_stop_decay", "to": 0.5, "rollback_to": 0.0,
        "meta_key": "h436_trial", "task": "DSH_HFT_H436_JUDGE",
        "title": "#16③ 止损后衰减通电（post_stop_decay 0→0.5，h396）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 连续止损次数下降",
    },
    "h437": {
        "field": "params.p1_hold_sec", "to": 60.0, "rollback_to": 0.0,
        "meta_key": "h437_trial", "task": "DSH_HFT_H437_JUDGE",
        "title": "P1 形态持有期通电（p1_hold_sec 0→60，h404）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10",
    },
    "h438": {
        "field": "params.p45_hold_sec", "to": 300.0, "rollback_to": 0.0,
        "meta_key": "h438_trial", "task": "DSH_HFT_H438_JUDGE",
        "title": "P4/P5 形态持有期通电（p45_hold_sec 0→300，h404）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 与硬上限交互无恶化",
    },
    "h439": {
        "field": "params.p3_spike_gate", "to": 0.3, "rollback_to": 0.0,
        "meta_key": "h439_trial", "task": "DSH_HFT_H439_JUDGE",
        "title": "P3 尖峰闸通电（p3_spike_gate 0→0.3，h401）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10",
    },
    "h440": {
        "field": "params.k_trend", "to": 0.5, "rollback_to": 0.0,
        "meta_key": "h440_trial", "task": "DSH_HFT_H440_JUDGE",
        "title": "趋势反向偏斜通电（k_trend 0→0.5，F204）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 顺势成交占比下降",
    },
    "h441": {
        "field": "params.ofi_flatten_threshold", "to": 0.5, "rollback_to": 0.0,
        "meta_key": "h441_trial", "task": "DSH_HFT_H441_JUDGE",
        "title": "F86 顺流离场通电（ofi_flatten_threshold 0→0.5）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C vwap_revert_up 回补腿亏损收窄",
    },
    "h442": {
        "field": "params.stop_ref_last_leg", "to": 1.0, "rollback_to": 0.0,
        "meta_key": "h442_trial", "task": "DSH_HFT_H442_JUDGE",
        "title": "止损参考价改最差入场（stop_ref_last_leg 0→1.0，h442 代码+单测）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 止损腿均值/尾部收窄、"
                    "正收益误平减少",
    },
    "h443": {
        "field": "params.compound_ratio", "to": 0.5, "rollback_to": 1.0,
        "meta_key": "h443_trial", "task": "DSH_HFT_H443_JUDGE",
        "title": "单腿名义减半（compound_ratio 1.0→0.5，薄盘 5 币宇宙敞口控制）",
        "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 止损腿 USD 损失减半、"
                    "峰值持仓名义/权益 ≤ 0.75",
    },
    "h448": {
        "field": "params.ofi_confirm_threshold", "to": 0.5, "rollback_to": 0.15,
        "meta_key": "h448_trial", "task": "DSH_HFT_H448_JUDGE",
        "title": "顺势确认阈 0.15→0.5（h447 约束优化：净/腿 −0.20→+0.03bp，腿速仅 160→148/h）",
        "criteria": "A legs/h≥0.8×基线（且 ≥60/h）；B Welch α=0.10；"
                    "C 净/腿转正、止损笔数下降",
    },
    "h452": {
        "field": "params.trend_only_bp", "to": 30.0, "rollback_to": 0.0,
        "meta_key": "h452_trial", "task": "DSH_HFT_H452_JUDGE",
        "title": "趋势下界要求 |r300|≥30（固定绝对值——已因频率事故回滚）",
        "criteria": "A legs/h≥60/h（硬约束，破则回滚）；B Welch α=0.10；C 净/腿 ≥ +0.5bp",
    },
    "h454": {
        "field": "params.trend_only_q", "to": 0.35, "rollback_to": 0.0,
        "meta_key": "h454_trial", "task": "DSH_HFT_H454_JUDGE",
        "title": "自适应趋势门槛（trend_only_q 0→0.35，≈|r300|≥30bp 但随市场自适应）",
        "criteria": "A legs/h≥60/h（硬约束）；B Welch α=0.10（vs 部署前基线）；"
                    "C 净/腿 ≥ +0.3bp；D 安静时段不熔断（近 1h 腿数 > 0）",
    },
    "h463": {
        "field": "params.max_one_side_seconds", "to": 90.0, "rollback_to": 45.0,
        "meta_key": "h463_trial", "task": "DSH_HFT_H463_JUDGE",
        "title": "加仓窗口 45→90s（h461：被动出场 30-60s 净 −0.83bp vs 更长窗更好）",
        "criteria": "A legs/h≥60/h；B Welch α=0.10；C **机制**：`legs_per_trip`"
                    "（腿数÷平仓腿数）应上升——加仓窗翻倍若不起作用则该值不变；"
                    "D 两半窗口方向一致（R55 加入，判定前预注册）；"
                    "⚠️ 原判据「30–60s 桶净额」无法从账本算（出场腿无持仓年龄字段），"
                    "已在判定前替换为 `legs_per_trip`（见 `h547_exit_age_probe.py`）",
        # [R55] 与 ③ 一致：两半方向一致 ⇒ 识别"效应只存在于某一半"的伪结论
        "two_halves": True,
        # [h484 2026-09-29] **已知制度断点**：本试跑窗内发生了两次与 h463 无关、
        # 但影响远大于它的变化 ⇒ 净/腿的 Welch 对比**不具备因果性**：
        #   · 18:32:15Z worker 重启：把持仓中位由 626s 修到 76s、300s 硬顶由 31 次降到 0
        #     （见 研究结论/手续费根因与被动出场_20260929.md §2）；
        #   · 18:33:15Z h472 部署：ofi_flatten 由 taker 改 maker-only，手续费/腿 −38%。
        # 护栏语义：窗口内命中断点 ⇒ **负向 Δ 不判 ROLLBACK，降级为 INCONCLUSIVE**
        # （频率崩塌 A 仍照常回滚——那是可客观测量的硬约束）。
        # 理由：这个仓库已经因为"跨 regime 基线"误杀过 4 个有证据的功能，
        # 不能再让一次已知污染的对比去静默回滚参数。
        "known_regime_breaks": ["2026-09-28T18:32:15+00:00",
                                "2026-09-28T19:48:47+00:00"],   # 第二次重启（探针+权威翻转）
    },
    "h464": {
        # [h483 2026-09-29] **取值修正：0.5→0.7 改为 0.5→0.9**。
        #
        # 原值 0.7 来自 h447 曲线里的"净/腿 +0.03→+0.14bp"那两行，但 h447 的网格
        # **没有 0.70 这一点**（GRID=0/0.1/0.15/0.2/0.25/0.3/0.4/0.5/0.6/0.8），
        # "+0.14bp" 实际是 **θ=0.8** 那一行（0.6 那点反而更差：+2.30 vs +2.33）
        # ⇒ 取 0.7 属取点错误。
        #
        # 正确做法（h482/h483）：
        #   ① 用**互斥分带** Welch 检验（不能用嵌套集合均值差）：
        #      0.5→0.7: Δ=+0.24bp p=0.766 **不显著**（等于空转，白等 12h）；
        #      0.5→0.8: Δ=+1.35bp p=0.047；0.5→0.9: Δ=+1.91bp p<0.001。
        #   ② 前后两半**样本外复现**：只有 0.90 / 0.95 两半都显著为正。
        #   ③ 频率余量：0.90 保留 0.92×腿量（现场 78 → 71/h），0.95 只保留 0.84×
        #      （→65/h，夜间有破 60/h 硬约束风险）⇒ 取 **0.90**。
        #   ④ 结构洞见：`fo = OFI×d` 近乎**二值化** —— 13.1% 样本落在 [0.99,1.01)
        #      且边际 +3.03bp，而 0.4~0.99 的"中等确认"合计仅 4.6%、边际 ≈0 或为负
        #      ⇒ **confirm 态只有在订单流饱和时才有信息**，这正是本改动的机制。
        "field": "params.ofi_confirm_threshold", "to": 0.9, "rollback_to": 0.5,
        "meta_key": "h464_trial", "task": "DSH_HFT_H464_JUDGE",
        "title": "顺势确认阈值 0.5→0.9（h483 样本外：只有 OFI 饱和态有边际；"
                 "两半独立复现 Δ≈+2bp、腿速保留 0.92×）",
        "criteria": "A legs/h≥60/h（硬约束）；B Welch α=0.10（净/腿）；"
                    "C **机制判据**：入场腿净/腿上升（confirm 只留饱和态）、"
                    "往返数不塌（≥0.85×基线）；D 两半窗口方向一致",
        # [R54] 打开"两半方向一致"的计算（此前 SPEC 写了该判据但**判定阶段从不计算**——
        # h483 只在研究阶段手工做过；不打开则 criterion D 在产物里查无实据）
        "two_halves": True,
    },
    "h472": {
        # [h472 2026-09-29] 不是调参，而是**换执行方式**：同一 ofi_flatten 信号，
        # 由"立即 taker 穿价"改为"撤加仓侧 + 挂被动减仓"。
        # 证据：h471（近 12h）手续费 = 净亏的 74%，出场**全是 taker**；
        #       ofi_flatten 单路径付 −3.53bp/腿费 + −1.19bp/腿穿价差、占手续费 49%；
        #       h470 该路径价格项 +4.89bp/腿、出场后 300s 离场方向有利漂移
        #       +9.91bp(t=2.15) ⇒ 该时点被动挂单本有对手方流量。
        # 需先重启 worker（新字段 `ofi_flatten_maker_only` 必须先在运行的类里存在，
        # 否则 `LaneRiskLimits(**{k for k in fields})` 会**静默丢弃**该键）。
        "field": "params.ofi_flatten_maker_only", "to": 1.0, "rollback_to": 0.0,
        "meta_key": "h472_trial", "task": "DSH_HFT_H472_JUDGE",
        "title": "ofi_flatten 执行改被动减仓（maker-only，省 taker 费 + 穿价差）",
        "criteria": "A legs/h≥60/h（硬约束）；B Welch α=0.10（净/腿）；"
                    "C taker 出场腿占比与费/腿显著下降（机制判据）；"
                    "D 未成交仓不越过 300s 合规硬顶",
        # [h490] 试跑窗内含两次 worker 重启（02:32 修持仓滞留、03:48 装探针+权威翻转）
        # ⇒ 净/腿的 Welch 对比不具因果性；负向 Δ 只降级 INCONCLUSIVE，不自动回滚
        # （频率硬约束仍照常回滚）。机制判据（taker_mix / compliance_backstop）不受影响。
        "known_regime_breaks": ["2026-09-28T18:32:15+00:00",
                                "2026-09-28T19:48:47+00:00"],
    },
    "h520": {
        # [h514 2026-09-29] **趋势闸关闭的应急预案**（不是现在部署）。
        #
        # 何时用：11:46 的 h454 判定（`trend_only_q` 0→0.35）若给出 INCONCLUSIVE
        # （框架不动作、闸门留在 0.35），则用本试跑把闸门关回 0 并走完整判定流程。
        #
        # 现场证据（非历史研究）：
        #   · h504（48h、904 往返）**单调反向**：|r300| 0-10bp 档往返 ≈ −0.00bp，
        #     20-40bp **−2.75**(t=−2.29)、≥40bp **−4.69**(t=−3.01)
        #     ⇒ "只在强趋势做"在本窗口内是反的；
        #   · h513（逐币×状态）好状态**因币而异**：BNB 偏好强趋势（+0.75/+1.77），
        #     NEAR 偏好弱趋势（0-10bp +4.01、≥40bp −7.07）⇒ 单一全局闸必对某部分币错；
        #   · h506：强趋势里被动回口从 86% 崩到 30%、止损从 1% 升到 16%。
        "field": "params.trend_only_q", "to": 0.0, "rollback_to": 0.35,
        "meta_key": "h520_trial", "task": "DSH_HFT_H520_JUDGE",
        "title": "趋势闸关闭（trend_only_q 0.35→0）：h504 单调反向 + h513 币间冲突",
        "criteria": "A legs/h≥60/h（硬约束）；B Welch α=0.10（净/腿）；"
                    "C 机制：弱趋势档往返净额不劣化、被动收口占比上升；D 腿速回升",
        "known_regime_breaks": ["2026-09-28T18:32:15+00:00",
                                "2026-09-28T19:48:47+00:00"],
    },
    "h522": {
        # [h514 2026-09-29] **陈旧挂单成交**（成交发生在挂出很久之后）是否更毒？
        #
        # 机制：引擎判定成交比真实穿越晚 ≈45s（h488 实测 p50=49s），且成交价记作
        # **当时的挂单价** ⇒ 若成交发生在挂出很久之后，那个价格锚定的是**旧中间价**。
        # 现场证据（h514，48h，仅在 `quote_ts` 覆盖的 666 笔入场腿上；覆盖率 52%）：
        #   · 挂单年龄 ≤30s：markout60 **−1.00bp**（560 笔，84.2%）
        #   · 30-45s：**+0.01bp**（68 笔）
        #   · 45-60s：**−6.22bp**（28 笔，t=−1.25）
        #   · 60-90s：**−7.23bp**（9 笔，t=−0.56）
        #   ⇒ 陈旧档比年轻档差 **−6.2bp**（样本薄、t 弱，但效应量大）。
        #
        # 处置思路：`max_quote_age_sec`（现 90s）**超过即丢弃挂单、不做成交判定**，
        # 下调到 45s 只砍掉最毒的那批成交（约 5.6% 的腿），不动其它逻辑。
        # 代价：腿数会掉 ~5%（当前 ~60/h ⇒ 可能压到地板下）⇒ 判据必须**同时**看
        # 频率与机制：陈旧档占比应降到 ≈0，且入场腿 markout 应改善。
        "field": "limits.max_quote_age_sec", "to": 45.0, "rollback_to": 90.0,
        "meta_key": "h522_trial", "task": "DSH_HFT_H522_JUDGE",
        "title": "丢弃陈旧挂单（max_quote_age_sec 90→45s）：砍掉最毒的 5.6% 成交",
        "criteria": "A legs/h≥60/h（硬约束）；B Welch α=0.10（净/腿）；"
                    "C 机制：年龄>45s 的成交占比应 ≈0（从 5.6% 降下来）；"
                    "D 入场腿 markout 应改善、止损腿数不上升",
        "known_regime_breaks": ["2026-09-28T18:32:15+00:00",
                                "2026-09-28T19:48:47+00:00"],
    },
    "h527": {
        # [h527 2026-09-29 用户裁决] 「改引擎：逐币几何 + 逐币规模（保住 ≥60/h）」。
        #
        # 为什么必须有逐币旋钮（h524，当前时代 15.79h，名义加权）：
        #   币    腿/h   净bp/腿   止损腿  taker占比
        #   BNB   30.8   −1.263      2     14.0%
        #   XRP   12.8   +0.134      2     11.9%
        #   NEAR  11.5   −3.526     22     33.1%
        #   ARB    5.8   −3.436      8     40.2%
        #   ENA    3.4   −2.777      8     41.5%
        # NEAR+ARB+ENA 只占 32% 的腿、却占 **90% 的止损腿**；但直接删币会把
        # 64.5 腿/h 打到 43.9（破 ≥60 硬约束）。
        #
        # **本 SPEC 只动一个旋钮**：`per_symbol_size_mult`（加仓腿目标名义的逐币倍数）。
        # 为什么是它而不是逐币挂宽：挂宽有频率弹性（h505 β=−0.91）⇒ 加宽会掉腿量；
        # 而**腿数是"事件数"、不是名义额** ⇒ 缩小单笔规模能在腿数不变的前提下
        # 把尾部 USD 亏损等比压下去，且敞口闸更晚触顶（该币可挂 tick 更多）。
        # 减仓腿**不受影响**（F91 精确平仓），否则会留残仓、把亏损搬去 taker 强平。
        #
        # ⚠️ 与 h522/③ 一样排在 ② 判定之后（单变量串行）；框架原先无法部署字典值
        # （`float(t)` 直接 TypeError）⇒ 已由 `scripts/h543_patch_trial_dict_params.py`
        # 补上 `_coerce_param`（只放宽字典，其余仍强制 float，既有 SPEC 行为不变）。
        "field": "limits.per_symbol_size_mult",
        "to": {"BNB": 0.5, "NEAR": 0.35, "ARB": 0.35, "ENA": 0.35},
        "rollback_to": {},                      # 空字典 ⇒ symbol_lookup 回退 1.0 ✓
        "meta_key": "h527_trial", "task": "DSH_HFT_H527_JUDGE",
        "title": "逐币单笔规模（BNB×0.5 + NEAR/ARB/ENA×0.35）：腿数不变、尾部 USD 亏损等比缩小",
        "criteria": "A legs/h≥60/h（硬约束）；"
                    "B **机制1**：被缩四币 单腿名义 P50 下降（BNB 预期 ≈×0.5、"
                    "其余 ≈×0.35）；"
                    "C **机制2**：这四币腿数不塌（≥0.85× 基线）——腿数是事件数，"
                    "缩规模不该掉腿；"
                    "D **机制3**：止损 USD 下降、四币净额 USD 亏损缩小；"
                    "E 净 bp/腿 不应显著变差（规模不该改变 bp/腿；若变差说明另有原因）；"
                    "F 对照：XRP（唯一正 bp/腿的币）**未被改动**，其腿数/净额应保持",
        # [R50 修订] 原 SPEC 只缩 NEAR/ARB/ENA。复核发现 **BNB 才是最大单一亏损源**：
        #   当前时代（15.79h）BNB 486 腿、加权 −1.263bp/腿（t=−2.67，唯一显著）、
        #   −0.485$/h = 总亏损的 46%（h509 的 72h 窗独立复现：−0.77bp/腿、t=−5.33）。
        #   而 BNB 占 48% 的腿量 ⇒ 直接删币会破 ≥60/h；**缩规模不改变腿数**（腿是事件数），
        #   故把 BNB 也纳入（×0.5 比三币的 ×0.35 温和，因它的每腿渗漏更小）。
        "known_regime_breaks": ["2026-09-29T01:09:00+00:00"],   # 04:32–09:09 停摆
    },
    # [R203 2026-09-29 **用户裁决 A**] ③ 的**忠实实现**：饱和要求闸（不是改 h464）。
    #
    # 为什么不能沿用 ③ 的 h464 SPEC：`ofi_confirm_threshold` 属**禁令**族（`|ofi| > θ` ⇒ 封逆势侧）
    #   ⇒ **调高 = 放松**，与 h483 的"要求"口径（只保留 `fo ≥ θ`）**方向相反** ✗；而且它带
    #   `and allow_sell/allow_buy` 前置守卫、被更早的趋势闸（`trend_pause_bp=15`）抢先 ⇒
    #   实测 3500 次拦截里 **0 次**（R201）⇒ 改它**测不到任何东西** ✗✗。
    # 本 SPEC 改的是新字段 `limits.ofi_require_threshold`（要求族：`|ofi| < θ` ⇒ **不建仓**，
    #   空仓时两侧都封、**减仓侧豁免**（F76）），这正是 h483 样本外结论的口径 ✓。
    #
    # ⚠️ 部署前置（硬条件）：新字段必须先**重启 worker** 才存在于运行态，否则
    #   `LaneRiskLimits(**{k: v for k in __dataclass_fields__})` 会**静默丢弃**它 ⇒ 部署=空转 ✗
    #   （h472 的教训）。部署顺序见 `scripts/h615_activation_preflight.py` 的打印。
    "h529": {
        "field": "limits.ofi_require_threshold", "to": 0.9, "rollback_to": 0.0,
        "meta_key": "h529_trial", "task": "DSH_HFT_H529_JUDGE",
        "title": "饱和要求闸（|ofi| < 0.9 ⇒ 不建仓）：h483 的「要求」口径首次可执行",
        "criteria": "A legs/h≥60/h（硬约束，可交易口径）；B Welch α=0.10（净/腿）；"
                    "C **机制（行为侧、不依赖旧基线 ⇒ 免疫缝隙变更）**："
                    "`GATE_PROBES['ofi_require_blocked']`（心跳 `gate_probe_counts`）"
                    "按 tick 计的占比应显著 >0 且随 θ 生效 —— 读数 `scripts/h612_confirm_gate_share.py`；"
                    "若它为 0 而 `h481` 回显 0.9 ⇒ 按 **F189**（改了没生效）处置：试跑作废 ✗；"
                    "D 两半窗口方向一致（R55）；E 腿量保留率 ≈0.92×（h483 的估计，作参考不作判据）",
        "two_halves": True,
    },
}


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def _append_ops(meta: dict, entry: dict) -> dict:
    ops = list(meta.get("ops_changes") or [])
    ops.append(entry)
    meta["ops_changes"] = ops[-20:]
    return meta


def _covered_hours(since: str, until: str, syms: list) -> tuple:
    """窗口内**真正可交易的时长**（小时）与覆盖率。

    [R34 2026-09-29] 为什么需要：当天代理节点反复失效，`market_trades_aggregated`
    出现外部停摆段 ⇒ 停摆小时的"零腿"被算进 `legs_per_hour` 的分母，会把**外部事故
    记到引擎/参数头上**（与 h500 的"市场变冷不该记到被测试参数头上"同一病根；
    且实测 h472 因此在 14:33 险些被判 `engine_stall` 无条件回滚 ✗）。

    口径：以**逐分钟**为单位统计 `market_trades_aggregated`（**引擎判定成交真正读的表**；
    `symbol` 在该表里是**裸币名**如 `XRP`）中有该币数据的分钟数
    ⇒ `可交易小时 = 分钟数/60`。
    ⚠️ 不用 `asterdex_trades`：它是原始逐笔，停机期间仍可能有其它写入方/回填
    （实测两表在 12h 窗内分别为 436/443 分钟，但**引擎只读聚合表** ⇒ 用逐笔表会把
    "引擎其实拿不到数据"的时间算成可交易）。
    取不到（行情库不可达）⇒ 返回 `(None, None)`，调用方**退回旧行为**（语义不变 ✓）。

    安全阀：覆盖率过低时**不得据此下判决**（见 `do_judge` 里的 `insufficient_covered_time`），
    否则等于用十几分钟的数据去判 12 小时的功能。
    """
    try:
        import psycopg as _pg
        mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
        bare = [str(s).replace("USDT", "") for s in syms]
        t0 = int(dt.datetime.fromisoformat(since).timestamp() * 1000)
        t1 = int(dt.datetime.fromisoformat(until).timestamp() * 1000)
        with _pg.connect(mk, autocommit=True) as cm:
            with cm.cursor() as cur:
                cur.execute(
                    "SELECT count(DISTINCT date_trunc('minute',"
                    " to_timestamp(timestamp/1000.0)))"
                    " FROM market_trades_aggregated WHERE symbol = ANY(%s)"
                    " AND timestamp > %s AND timestamp <= %s",
                    (bare, t0, t1))
                mins = int(cur.fetchone()[0] or 0)
        wall_h = max((dt.datetime.fromisoformat(until)
                      - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0, 1e-6)
        cov_h = mins / 60.0
        return cov_h, min(1.0, cov_h / wall_h)
    except Exception as exc:  # noqa: BLE001
        print(f"[R34] 可交易时长取数失败（退回旧行为）：{type(exc).__name__}: {exc}")
        return None, None


def _market_activity(since, until, syms):
    """[h500 2026-09-29] 用**真实逐笔**度量窗口内的市场活跃度与波动（与引擎无关）。

    为什么频率判据需要它：
      用户硬约束是"≥60 腿/h"，本框架此前对**绝对地板**是无条件回滚。
      但实测（2026-09-29 04:0x）腿速掉到 12–27/h 的同时，
      拦截画像是 `vol_pause(σ=1.93) + vol_regime + 趋势闸` ≈ 86% 的决策
      ⇒ 是**风控闸在高波动里按设计收紧**，不是某个试跑参数把引擎掐死。
      若照旧机械回滚，就会把"市场/波动"的账记到被测试的参数头上
      （与早先"跨 regime 基线误杀 4 个功能"同族）。

    返回 {trades_per_h, vol_bp}：
      · trades_per_h = 真实成交笔数 / 小时（活跃度）；
      · vol_bp       = 每分钟最后成交价的一阶差分绝对值的均值（bp，波动代理）。
    任一项取不到 ⇒ 返回 {}，调用方退回旧行为（无条件地板的语义不变）。
    """
    try:
        import psycopg as _pg
        mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
        pairs = [str(s) + "USDT" for s in syms]
        with _pg.connect(mk, autocommit=True) as cm:
            with cm.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM asterdex_trades WHERE symbol = ANY(%s) "
                    "AND event_ts_ms > %s AND event_ts_ms <= %s",
                    (pairs, int(dt.datetime.fromisoformat(since).timestamp() * 1000),
                     int(dt.datetime.fromisoformat(until).timestamp() * 1000)))
                n = int(cur.fetchone()[0] or 0)
                cur.execute(
                    # ⚠️ 必须**按币分区**：第一版只按分钟聚合 ⇒ 把 BNB(≈760) 与 XRP(≈1.49)
                    # 的价差混在一起，vol_bp 算出 98 万 bp 这种荒谬值。
                    "WITH m AS (SELECT symbol, "
                    "date_trunc('minute', to_timestamp(event_ts_ms/1000.0)) AS mi, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 AS px "
                    "FROM asterdex_trades WHERE symbol = ANY(%s) "
                    "AND event_ts_ms > %s AND event_ts_ms <= %s GROUP BY 1, 2), "
                    "d AS (SELECT px, LAG(px) OVER (PARTITION BY symbol ORDER BY mi) AS p0 "
                    "FROM m) "
                    "SELECT COALESCE(AVG(ABS(px-p0)/NULLIF(p0,0))*1e4, 0)::float8 "
                    "FROM d WHERE p0 IS NOT NULL",
                    (pairs, int(dt.datetime.fromisoformat(since).timestamp() * 1000),
                     int(dt.datetime.fromisoformat(until).timestamp() * 1000)))
                vol = float(cur.fetchone()[0] or 0.0)
        hrs = max((dt.datetime.fromisoformat(until)
                   - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0, 1e-6)
        return {"trades_per_h": n / hrs, "vol_bp": vol, "trades": n}
    except Exception as exc:  # noqa: BLE001
        print(f"[h500] 市场活跃度取数失败（退回旧行为）：{type(exc).__name__}: {exc}")
        return {}


def _welch(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = sum(a) / n1, sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return None
    t = (m1 - m2) / se
    df = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))

    def _pdf(x):
        return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) \
            / math.sqrt(math.pi * df) * (1 + x * x / df) ** (-(df + 1) / 2)

    step, s, x = 0.05, 0.0, abs(t)
    while x < abs(t) + 30.0:
        s += step * (_pdf(x) + _pdf(x + step)) / 2
        x += step
    return {"t": t, "p": min(1.0, 2 * s), "mean_trial": m1, "mean_base": m2,
            "n_trial": n1, "n_base": n2, "delta": m1 - m2}


def _load_meta(cur):
    cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
    row = cur.fetchone()
    if not row:
        print(f"✗ 车道 {LANE} 不存在")
        raise SystemExit(1)
    return json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})


# ── [h459 2026-09-29] **跨进程元信息锁** ──────────────────────────────────────
# 事故：5 个判定任务并行运行，各自 read-modify-write 整个 meta_json ⇒ **读改写竞争**，
# 后写者覆盖先写者 ⇒ 判定静默丢失（h429、h433/h435/h437/h438 实测；判定 JSON 里有、
# 注册表里没有）。修复：所有对 lane_registry.meta_json 的读改写都套文件锁串行化。
# 陈旧锁（>120s，持有者崩溃）自动清理。
LOCK_PATH = ROOT / "logs" / "lane_meta.lock"


class _MetaLock:
    def __enter__(self):
        import os
        import time
        for _ in range(180):
            try:
                fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - LOCK_PATH.stat().st_mtime > 120:
                        LOCK_PATH.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass
                time.sleep(0.5)
        raise TimeoutError("lane_meta 锁超时（>90s）：另一进程可能卡住")

    def __exit__(self, *exc):
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _with_meta_lock(fn):
    """把整个 deploy/rollback/judge 串行化（跨进程文件锁）。"""
    def _wrapped(*args, **kwargs):
        with _MetaLock():
            return fn(*args, **kwargs)
    _wrapped.__name__ = fn.__name__
    return _wrapped


def _save(cur, c, meta):
    cur.execute("UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
    c.commit()


def _coerce_param(v):
    """SPEC 里的参数值：数值 → `float`；**字典（逐币参数）原样通过**。

    [h527 2026-09-29] 为什么需要：h527 的逐币旋钮值是 `{"NEAR": 0.35, ...}` 这类字典，
    而 `do_deploy/do_rollback/do_judge` 原先一律 `float(t)` ⇒ 遇字典直接 `TypeError`，
    等于**框架无法部署逐币参数** ⇒ 只能手写登记表，那就绕过了
    「预注册 → 试跑 → 判定 → 自动回滚」整条治理链（本仓库最贵的教训就是这类"绕过去"）。

    这里**只放宽"字典"这一种形态**（并对字典内的值仍逐个强制 float），
    其余分支行为与改动前逐字一致 ⇒ 既有 SPEC 全部不受影响 ✓。
    """
    if isinstance(v, dict):
        out: dict = {}
        for k, x in v.items():
            try:
                out[str(k)] = float(x)
            except (TypeError, ValueError):
                raise SystemExit(f"✗ SPEC 字典参数的 {k!r} 不是数值：{x!r}")
        return out
    return float(v)


def _current_of(params: dict, name: str, default=0.0):
    """取该参数**当前值**用于审计：字典原样返回，数值转 float，缺失/非法 ⇒ default。"""
    v = params.get(name)
    if isinstance(v, dict):
        return v
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _schedule_judge(task: str, script: str, key: str, at: dt.datetime) -> int:
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           rf"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py --trial {key} --judge")
    _r = subprocess.run(
        ["schtasks", "/Create", "/TN", task, "/TR", _tr,
         "/SC", "ONCE", "/ST", at.strftime("%H:%M"), "/SD", at.strftime("%Y/%m/%d"), "/F"],
        capture_output=True, text=True, timeout=60)
    # [R196] `schtasks /Create` **没有** StartWhenAvailable 开关 ⇒ 新建的判定任务默认
    # `StartWhenAvailable=False`：若触发时刻机器在睡/关机，任务**被跳过且不会补跑** ⇒
    # 判定永不落地 ⇒ 串行链只能反复改期（静默停摆）。R114 是把**当时已存在**的四个关键任务
    # 手工改成 True 的（实测：`H463_JUDGE`=True，而更早由本函数建的 `H425_JUDGE`=False ✗）
    # ⇒ 每次新部署出来的判定任务都会**退回 False**。本函数补上这一步。
    # 安全性：① 只**补一条设置**，创建本身已完成；② 失败**不致命**（返回码仍以创建为准，
    # 只记录），因为"任务建好了但没这层冗余"远好于"因为设置失败而没建任务"；
    # ③ 显式给出 MultipleInstances/ExecutionTimeLimit，避免 `New-ScheduledTaskSettingsSet`
    # 用默认值覆盖既有语义（取值与现存任务一致：IgnoreNew / PT72H）。
    try:
        _ps = ("$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
               "-MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 72); "
               f"Set-ScheduledTask -TaskName '{task}' -Settings $s | Out-Null")
        _r2 = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _ps],
                             capture_output=True, text=True, timeout=90)
        if _r2.returncode != 0:
            print(f"⚠️ 判定任务的 StartWhenAvailable 未能设置（rc={_r2.returncode}）"
                  f"——任务本身已创建，判定仍会跑；仅少了「错过触发后补跑」这层冗余")
    except Exception as _e:  # noqa: BLE001
        print(f"⚠️ 设置 StartWhenAvailable 异常（不致命）：{type(_e).__name__}: {_e}")
    return _r.returncode


@_with_meta_lock
def do_deploy(a, spec, key) -> int:
    import psycopg
    field = spec["field"]
    # [h432] 多字段支持：fields = [(name, to, rollback), ...]（单字段 spec 自动展开）
    fields = [(f.split(".")[-1], _coerce_param(t), _coerce_param(rb))
              for f, t, rb in (spec.get("fields") or [(field, spec["to"], spec["rollback_to"])])]
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = _load_meta(cur)
            params = dict(meta.get("params") or {})
            old = {name: _current_of(params, name, 0.0) for name, *_ in fields}
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

            guards = []
            for k in SERIAL_KEYS:
                t = dict(meta.get(k) or {})
                if t.get("started_at") and t.get("verdict") in (None, "INCONCLUSIVE"):
                    guards.append(f"{k} 进行中 verdict={t.get('verdict')!r}（出场侧串行）")
            _h411 = dict(meta.get("h411_trial") or {})
            if _h411.get("verdict") not in ("PASS", "ROLLBACK"):
                guards.append(f"h411_trial.verdict={_h411.get('verdict')!r}（需 PASS/ROLLBACK 终判）")
            if guards and not a.force:
                print("✗ 部署守卫拒绝：")
                for g in guards:
                    print(f"    - {g}")
                print("   若确需强制：--force（trial meta 会记 guards_bypassed）")
                return 3

            for name, to, _rb in fields:
                params[name] = to
            meta["params"] = params
            # [h433 2026-09-28 用户裁决] **不得在部署时重写 `stats_since`**：
            # 手续费/已实现是**账户级**口径，只有「重置整个模拟账户」才应一起清零；
            # 每次参数部署都重置 ⇒ 面板数字被反复清零、与余额永远对不上
            # （用户实测："账户不重置，光重置手续费记录"✗）。
            # 试跑判定用各自的 `h*_trial.started_at` 裁剪，不需要 stats_since。
            meta = _append_ops(meta, {
                "ts": now_iso, "action": f"{key}_deploy",
                "field": field, "from": old, "to": {n: t for n, t, _ in fields},
                "note": (f"{spec['title']}。判定=T+12h；回滚=--rollback（热采用 ≤60s）；"
                         + ("【guards_bypassed：--force（用户全面修复委任）】" if guards else "")),
            })
            meta[spec["meta_key"]] = {
                "started_at": now_iso, "from": old,
                "to": {n: t for n, t, _ in fields},
                "baseline_symbols": [str(s) for s in (meta.get("symbols") or []) if str(s)],
                "rollback_to": {n: rb for n, _, rb in fields},
                "judge_at": (dt.datetime.now(dt.timezone.utc)
                             + dt.timedelta(hours=12)).isoformat(),
                "criteria": spec["criteria"],
                "guards_bypassed": bool(guards and a.force),
            }
            _save(cur, c, meta)

    print(f"✓ {key} 部署：{ {n: (old[n], t) for n, t, _ in fields} }（热采用 ≤60s，无需重启）")
    _nxt = dt.datetime.now() + dt.timedelta(hours=12)
    rc = _schedule_judge(spec["task"], "h425_repair_trial.py", key, _nxt)
    print(f"判定任务 {spec['task']} @ {_nxt:%Y-%m-%d %H:%M} rc={rc}")
    return 0 if rc == 0 else 4


@_with_meta_lock
def do_rollback(a, spec, key) -> int:
    import psycopg
    field = spec["field"]
    fields = [(f.split(".")[-1], _coerce_param(t), _coerce_param(rb))
              for f, t, rb in (spec.get("fields") or [(field, spec["to"], spec["rollback_to"])])]
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = _load_meta(cur)
            params = dict(meta.get("params") or {})
            old = {name: _current_of(params, name, 0.0) for name, *_ in fields}
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            for name, _to, rb in fields:
                params[name] = rb
            meta["params"] = params
            # [h433] 回滚同样不重置 stats_since（账户级口径不动，见 deploy 处注释）
            meta = _append_ops(meta, {
                "ts": now_iso, "action": f"{key}_rollback",
                "field": field, "from": old, "to": {n: rb for n, _, rb in fields},
                "note": f"{spec['title']} 回滚（热采用 ≤60s）"})
            meta[spec["meta_key"]] = {**dict(meta.get(spec["meta_key"]) or {}),
                                      "rolled_back_at": now_iso, "verdict": "ROLLBACK",
                                      "why": "manual_rollback"}
            _save(cur, c, meta)
    print(f"✓ {key} 回滚：{field} 已恢复（热采用）")
    return 0


def _per_leg(cur, since, until, syms):
    cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz "
                "AND ts <= %s::timestamptz AND symbol = ANY(%s)",
                (LANE, since, until, syms))
    return [float(r[0] or 0.0) for r in cur.fetchall()]


def _window(cur, since, until, hours, syms):
    cur.execute("SELECT count(*), COALESCE(sum(net_bp),0.0)::float8 FROM lane_ledger "
                "WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz "
                "AND symbol = ANY(%s)",
                (LANE, since, until, syms))
    r = cur.fetchone()
    legs = int(r[0] or 0)
    return {"legs": legs, "net_bp": float(r[1] or 0.0),
            "legs_per_hour": legs / max(hours, 0.01)}


def _sub_stats(cur, key, since, until, syms):
    """子口径：h425 止损尾部+jump 腿；h426 止损计数；h427 硬上限计数+仓龄。"""
    out = {}
    if key in ("h425", "h426"):
        cur.execute(
            "SELECT count(*), COALESCE(avg(net_bp),0.0)::float8, "
            "COALESCE(min(net_bp),0.0)::float8 FROM lane_ledger"
            " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            " AND meta_json->>'exit_path' LIKE 'stop_loss%%' AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["stop"] = {"n": int(r[0] or 0), "mean_bp": float(r[1] or 0.0),
                       "min_bp": float(r[2] or 0.0)}
    if key == "h425":
        cur.execute(
            "SELECT count(*), COALESCE(avg(net_bp),0.0)::float8 FROM lane_ledger"
            " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            " AND meta_json->>'exit_path' LIKE 'jump_exit%%' AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["jump_exit"] = {"n": int(r[0] or 0), "mean_bp": float(r[1] or 0.0)}
    if key == "h427":
        cur.execute(
            "SELECT count(*), COALESCE(avg(net_bp),0.0)::float8 FROM lane_ledger"
            " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            " AND meta_json->>'exit_path' = 'timeout_hard_taker' AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["hard_cap"] = {"n": int(r[0] or 0), "mean_bp": float(r[1] or 0.0)}
        cur.execute(
            "SELECT count(*), COALESCE(avg((meta_json->>'qty')::float8 * "
            "(meta_json->>'fill_px')::float8),0.0)::float8 FROM lane_ledger"
            " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            " AND meta_json->>'exit_path' IN ('stop_loss_taker','timeout_hard_taker')"
            " AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["force_exit_notional"] = {"n": int(r[0] or 0), "mean_usd": float(r[1] or 0.0)}
    if key == "h464":
        # [h483] 机制专属判据：confirm 阈值只作用于**入场**（把腿量投向 OFI 饱和态）
        # ⇒ 必须看"入场腿的净额/腿"与"往返数"是否随质量上升而改善，
        # 而不是只看受基线污染的总体净/腿（基线的 12h 窗里 ofi_confirm 还混着旧值 0.15）。
        cur.execute(
            "SELECT "
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')='') AS n_ent,"
            " COALESCE(avg(net_bp) FILTER (WHERE COALESCE(meta_json->>'exit_path','')=''),"
            "          0.0)::float8 AS net_ent,"
            " COALESCE(avg(fee_bp) FILTER (WHERE COALESCE(meta_json->>'exit_path','')=''),"
            "          0.0)::float8 AS fee_ent,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')<>'') AS n_exit,"
            " COALESCE(avg(net_bp) FILTER (WHERE COALESCE(meta_json->>'exit_path','')<>''),"
            "          0.0)::float8 AS net_exit "
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["entry_vs_exit"] = {
            "entry_legs": int(r[0] or 0), "net_bp_per_entry_leg": float(r[1] or 0.0),
            "fee_bp_per_entry_leg": float(r[2] or 0.0),
            "exit_legs": int(r[3] or 0), "net_bp_per_exit_leg": float(r[4] or 0.0),
        }
        # 往返数代理：**出场腿数**（每次出场成交≈一次往返完成）。
        # ⚠️ 不能用 `count(DISTINCT position_id)`：实测 `position_id` 形如 `mm:XRP:1`
        # 会被**反复复用**（近 1h：出场腿 20 而 distinct position_id 只有 7）⇒ 严重低估。
        cur.execute(
            "SELECT count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')<>''), "
            " count(DISTINCT position_id) FILTER "
            "  (WHERE COALESCE(meta_json->>'exit_path','')<>'') "
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["round_trips"] = {"exit_legs_as_trips": int(r[0] or 0),
                              "distinct_position_id_unreliable": int(r[1] or 0)}
    if key == "h472":
        # 机制判据：taker 出场腿的数量与费率（maker 费率 0 ⇒ taker 费是纯支出）。
        cur.execute(
            "SELECT "
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker') AS n_taker,"
            " count(*) FILTER (WHERE meta_json->>'exit_path' <> '') AS n_exit,"
            " COALESCE(avg(fee_bp) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker'),"
            "          0.0)::float8 AS fee_taker,"
            " COALESCE(avg(fee_bp),0.0)::float8 AS fee_all,"
            " COALESCE(avg(net_bp) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker'),"
            "          0.0)::float8 AS net_taker "
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        n_taker, n_exit = int(r[0] or 0), int(r[1] or 0)
        out["taker_mix"] = {
            "taker_legs": n_taker, "exit_legs": n_exit,
            "taker_share": round(n_taker / n_exit, 4) if n_exit else None,
            "fee_bp_per_taker_leg": float(r[2] or 0.0),
            "fee_bp_per_leg_all": float(r[3] or 0.0),
            "net_bp_per_taker_leg": float(r[4] or 0.0),
        }
        cur.execute(
            "SELECT COALESCE(avg(CASE WHEN meta_json->>'exit_path' = 'timeout_hard_taker'"
            " THEN 1.0 ELSE 0.0 END),0.0)::float8,"
            " count(*) FILTER (WHERE meta_json->>'exit_path' = 'timeout_hard_taker')"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["compliance_backstop"] = {"hard_cap_legs": int(r[1] or 0)}
    if key == "h436":
        # [h436 R33] 判据 C：**连续止损次数下降**（post_stop_decay 的作用是"止损后
        # 该币 30min 内加仓腿名义衰减"⇒ 机制上应看到"同一币连续踩止损"变少）。
        # 口径：同一 symbol 的相邻止损腿间隔 <30min 记一次"连击"。
        cur.execute(
            "WITH s AS ("
            "  SELECT symbol, ts,"
            "         lag(ts) OVER (PARTITION BY symbol ORDER BY ts) AS prev_ts"
            "  FROM lane_ledger WHERE lane_id=%s"
            "    AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            "    AND meta_json->>'exit_path' LIKE 'stop_loss%%' AND symbol = ANY(%s))"
            " SELECT count(*) AS stops,"
            "   count(*) FILTER (WHERE prev_ts IS NOT NULL"
            "     AND extract(epoch from ts) - extract(epoch from prev_ts) < 1800)"
            "   AS back_to_back,"
            "   COALESCE(avg(extract(epoch from ts) - extract(epoch from prev_ts))"
            "     FILTER (WHERE prev_ts IS NOT NULL),0)::float8 AS gap_p50_s"
            " FROM s",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _st, _bb = int(r[0] or 0), int(r[1] or 0)
        out["stop_chaining"] = {
            "stops": _st, "back_to_back_30min": _bb,
            "back_to_back_share": round(_bb / _st, 4) if _st else None,
            "mean_gap_s": round(float(r[2] or 0.0), 1),
        }
    if key == "h442":
        # [h442 R33] 判据 C：**止损腿均值/尾部收窄、正收益误平减少**。
        # 全部账本可得：均值 + p10（尾部）+ "止损却为正收益"的腿数（误平）。
        cur.execute(
            "SELECT count(*), COALESCE(avg(net_bp),0)::float8,"
            " COALESCE(percentile_cont(0.1) WITHIN GROUP (ORDER BY net_bp),0)::float8,"
            " count(*) FILTER (WHERE net_bp > 0) AS false_stops"
            " FROM lane_ledger WHERE lane_id=%s"
            " AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            " AND meta_json->>'exit_path' LIKE 'stop_loss%%' AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["stop_shape"] = {"stops": int(r[0] or 0),
                             "mean_bp": round(float(r[1] or 0.0), 3),
                             "p10_bp": round(float(r[2] or 0.0), 3),
                             "false_stops_positive": int(r[3] or 0)}
    if key == "h443":
        # [h443 R33] 判据 C：**止损腿 USD 损失减半、峰值持仓名义下降**。
        # USD 用 `net_bp × notional`；峰值持仓名义用单腿名义的 p95 作代理
        # （真实峰值需回放持仓轨迹；这里给出可逐窗对比的可得口径）。
        cur.execute(
            "SELECT COALESCE(sum(net_bp*notional/1e4) FILTER ("
            "         WHERE meta_json->>'exit_path' LIKE 'stop_loss%%'),0)::float8,"
            " COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY notional),0)::float8,"
            " COALESCE(avg(notional),0)::float8, count(*)"
            " FROM lane_ledger WHERE lane_id=%s"
            " AND ts > %s::timestamptz AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["size_shape"] = {"stop_usd": round(float(r[0] or 0.0), 3),
                             "notional_p95": round(float(r[1] or 0.0), 2),
                             "notional_mean": round(float(r[2] or 0.0), 2),
                             "legs": int(r[3] or 0)}
    if key == "h454":
        # [h454 R33] 判据 D：**安静时段不熔断**（趋势闸不能把车道整个封死）。
        # 口径：窗口内相邻腿的最大间隔——>60min 即视为"疑似熔断"。
        cur.execute(
            "SELECT count(*),"
            " COALESCE(max(extract(epoch from ts) - extract(epoch from prev_ts)),0)::float8"
            " FROM (SELECT ts, lag(ts) OVER (ORDER BY ts) AS prev_ts"
            "       FROM lane_ledger WHERE lane_id=%s"
            "         AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            "         AND symbol = ANY(%s)) t",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _gap = float(r[1] or 0.0)
        out["no_blackout"] = {"legs": int(r[0] or 0),
                              "max_gap_min": round(_gap / 60.0, 1),
                              "blackout_suspected": bool(_gap > 3600)}
    if key in ("h426", "h429"):
        # [R65] 补齐休眠 SPEC 的机制判据（`h548` 审计里登记为"有判据无实现"）：
        #   h426「平静段止损笔数下降、止损腿均值不变差」
        #   h429「止损笔数下降、`sudden_move` 腿均值不恶化」
        # 两者的共同口径 = 止损计数与均值 + sudden_move 计数与均值，故合并实现。
        cur.execute(
            "SELECT"
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stops,"
            " COALESCE(avg(net_bp) FILTER (WHERE meta_json->>'exit_path'"
            "   LIKE 'stop_loss%%'),0)::float8 AS stop_mean,"
            " count(*) FILTER (WHERE meta_json->>'exit_reason' LIKE 'sudden_move%%')"
            "   AS sudden,"
            " COALESCE(avg(net_bp) FILTER (WHERE meta_json->>'exit_reason'"
            "   LIKE 'sudden_move%%'),0)::float8 AS sudden_mean"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        out["stops_and_sudden"] = {
            "stop_legs": int(r[0] or 0), "stop_net_bp": round(float(r[1] or 0.0), 3),
            "sudden_move_legs": int(r[2] or 0),
            "sudden_move_net_bp": round(float(r[3] or 0.0), 3)}
    if key == "h432":
        # [R65] h432「强平笔数下降、taker 费占比下降」。
        # ⚠️ 口径校准（h567 实测）：`meta_json->>'flatten'` **不是**强平标记——它数出 224 腿，
        # 而 `exit_path LIKE 'ofi_flatten%'` 只有 114 腿（h472 运行时计数 105 taker + 9 maker
        # 与之精确吻合）⇒ 强平必须用 `exit_path`，`flatten` 疑为"该腿把持仓平到零"。
        cur.execute(
            "SELECT"
            " count(*) FILTER (WHERE meta_json->>'exit_path'"
            "   LIKE 'ofi_flatten%%') AS ofi_flatten,"
            " count(*) FILTER (WHERE meta_json->>'exit_path'"
            "   LIKE '%%taker') AS taker_legs,"
            " COALESCE(sum(fee_bp*notional/1e4),0)::float8 AS fee_usd,"
            " COALESCE(sum(net_bp*notional/1e4),0)::float8 AS net_usd,"
            " COALESCE(sum(fee_bp*notional/1e4) FILTER (WHERE meta_json->>'exit_path'"
            "   LIKE '%%taker'),0)::float8 AS taker_fee_usd"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _fee, _net = float(r[2] or 0.0), float(r[3] or 0.0)
        out["force_exit_mix"] = {
            "ofi_flatten_legs": int(r[0] or 0), "taker_legs": int(r[1] or 0),
            "fee_usd": round(_fee, 3),
            "taker_fee_usd": round(float(r[4] or 0.0), 3),
            "fee_share_of_net": (round(abs(_fee) / abs(_net), 4) if _net else None)}
    if key == "h440":
        # [R65] h440「顺势成交占比下降」：账本无"顺势/逆势"标记 ⇒ 用**成交后价格是否沿
        # 持仓方向继续走**的可得代理：以 `price_bp`（该腿的价格项）符号——顺势成交的
        # 价格项应更差（这正是该闸要削的对象）。⚠️ 属替代口径，判定前必须登记。
        cur.execute(
            "SELECT count(*),"
            " count(*) FILTER (WHERE price_bp < 0) AS neg_price,"
            " COALESCE(avg(price_bp),0)::float8,"
            " COALESCE(avg(price_bp) FILTER (WHERE price_bp < 0),0)::float8"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _n = int(r[0] or 0)
        out["trend_alignment_proxy"] = {
            "legs": _n, "neg_price_legs": int(r[1] or 0),
            "neg_price_share": (round(int(r[1] or 0) / _n, 4) if _n else None),
            "price_bp_mean": round(float(r[2] or 0.0), 3),
            "price_bp_mean_neg": round(float(r[3] or 0.0), 3),
            "note": "替代口径：账本无顺势/逆势标记 ⇒ 用 price_bp<0 的占比代理"}
    if key in ("h463", "h529"):
        # [h463 2026-09-29] 机制口径：本改动放宽**加仓封锁年龄上限**（45→90s）
        # ⇒ 机制上应当看到"仓位能攒更多腿、且出场构成改变"。
        #
        # ⚠️ 预注册的原始判据 C 是「30–60s 桶净额转正」（来自 h461），但它**无法从账本算**：
        #   出场腿的 `meta_json` 里**没有持仓年龄字段**（实测键只有 exit_path/quote_ts/
        #   qty/fill_px/... 见 `h547_exit_age_probe.py`）⇒ 要算年龄必须用
        #   "带符号数量累计归零"重建往返（h494 口径），成本高且引入新的配对假设。
        # ⇒ **在判定之前**（21:24 之前）改用账本能直接回答的等价机制代理：
        #   `legs_per_trip`（腿数 ÷ 真实平仓腿数）= 每趟往返攒了几条腿。
        #   加仓窗翻倍 ⇒ 该值应上升；若不变，说明 45s 从来不是约束（=该参数没起作用）。
        # 这是**替换**不是放宽：原判据的意图（更长窗是否有用）由 legs_per_trip 直接回答。
        cur.execute(
            "SELECT count(*) AS legs,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'flatten','') = 'true') AS exits,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') LIKE '%%taker')"
            "   AS taker_legs,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
            "   LIKE 'timeout%%') AS timeout_legs,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
            "   LIKE 'stop_loss%%') AS stop_legs,"
            " COALESCE(avg(net_bp) FILTER ("
            "   WHERE COALESCE(meta_json->>'exit_path','') = ''),0)::float8 AS entry_net_bp"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _legs, _exits = int(r[0] or 0), int(r[1] or 0)
        out["hold_window"] = {
            "legs": _legs, "exit_legs": _exits,
            "add_legs": _legs - _exits,
            "legs_per_trip": round(_legs / _exits, 4) if _exits else None,
            "taker_legs": int(r[2] or 0), "timeout_legs": int(r[3] or 0),
            "stop_legs": int(r[4] or 0),
            "entry_net_bp": round(float(r[5] or 0.0), 3),
            "note": ("判据 C 的预注册替换：账本无持仓年龄 ⇒ 用 legs_per_trip 代理"
                     "（判定前记录，见 h547）"
                     if key == "h463" else
                     "**h529 的口径**：本闸是「要求式」（|ofi| < θ ⇒ 不建仓）⇒ 账本侧应看到"
                     "**腿量小幅下降而往返不塌**（`legs` 与 `exit_legs` 之比即每趟腿数，"
                     "应与基线同量级）；**判据 C（行为侧探针 `GATE_PROBES`）不在账本里**，"
                     "由心跳读取：`scripts/h612_confirm_gate_share.py` / 验收块 ✓"),
        }
    if key == "h520":
        # [h520 R33] 机制：趋势闸关闭 ⇒ (i) **被动收口占比应上升**（不再过早穿价离场）、
        # (ii) 止损腿数不应上升。
        # ⚠️ 判据里的"弱趋势档往返净额"需要逐趟 |r300|（在**行情库**），而 `_sub_stats`
        #    只连核心库 ⇒ 用**被动/主动收口构成 + 止损腿数 + 入场腿净额**作可得代理，
        #    并在产物里留 `note` 说明替代关系（判定前登记）。
        cur.execute(
            "SELECT count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','') <> '')"
            "   AS exits,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
            "   LIKE '%%maker%%') AS maker_exits,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
            "   LIKE '%%taker%%') AS taker_exits,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
            "   LIKE 'stop_loss%%') AS stop_legs,"
            " COALESCE(avg(net_bp) FILTER ("
            "   WHERE COALESCE(meta_json->>'exit_path','') = ''),0)::float8 AS entry_net_bp"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _ex, _mk, _tk = int(r[0] or 0), int(r[1] or 0), int(r[2] or 0)
        out["exit_mix"] = {
            "exit_legs": _ex, "maker_exits": _mk, "taker_exits": _tk,
            "passive_share": round(_mk / _ex, 4) if _ex else None,
            "stop_legs": int(r[3] or 0),
            "entry_net_bp": round(float(r[4] or 0.0), 3),
            "note": "判据 C 的替代口径：逐趟 |r300| 在行情库不可得 ⇒ 用被动收口占比代理",
        }
    if key == "h522":
        # [h522 R33] 机制 C：**陈旧挂单成交占比**（年龄 = 判定时刻 ts − 挂单时刻 quote_ts）。
        # ⚠️ `quote_ts` 只覆盖一部分腿（h514 实测入场腿约 52%、出场腿常为 0）
        #    ⇒ 分母只算"有 quote_ts 的腿"，并把覆盖率一起报出来（否则占比会被静默稀释）。
        cur.execute(
            "SELECT"
            " count(*) AS legs,"
            " count(*) FILTER (WHERE COALESCE((meta_json->>'quote_ts')::float8,0) > 0)"
            "   AS with_q,"
            " count(*) FILTER ("
            "   WHERE COALESCE((meta_json->>'quote_ts')::float8,0) > 0"
            "     AND (extract(epoch from ts)"
            "          - (meta_json->>'quote_ts')::float8) > 45) AS stale45,"
            " count(*) FILTER ("
            "   WHERE COALESCE((meta_json->>'quote_ts')::float8,0) > 0"
            "     AND (extract(epoch from ts)"
            "          - (meta_json->>'quote_ts')::float8) > 90) AS stale90,"
            " percentile_cont(0.5) WITHIN GROUP (ORDER BY"
            "   (extract(epoch from ts) - (meta_json->>'quote_ts')::float8))"
            "   FILTER (WHERE COALESCE((meta_json->>'quote_ts')::float8,0) > 0)::float8"
            "   AS age_p50,"
            " count(*) FILTER (WHERE COALESCE(meta_json->>'exit_path','')"
            "   LIKE 'stop_loss%%') AS stop_legs,"
            " COALESCE(avg(net_bp) FILTER ("
            "   WHERE COALESCE(meta_json->>'exit_path','') = ''),0)::float8 AS entry_net_bp"
            " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
            " AND ts <= %s::timestamptz AND symbol = ANY(%s)",
            (LANE, since, until, syms))
        r = cur.fetchone()
        _legs, _wq = int(r[0] or 0), int(r[1] or 0)
        out["stale_quote"] = {
            "legs": _legs, "legs_with_quote_ts": _wq,
            "quote_ts_coverage": round(_wq / _legs, 4) if _legs else None,
            "stale_gt45": int(r[2] or 0), "stale_gt90": int(r[3] or 0),
            "stale_gt45_share_of_covered": (round(int(r[2] or 0) / _wq, 4) if _wq else None),
            "age_p50_s": round(float(r[4] or 0.0), 1) if r[4] is not None else None,
            "stop_legs": int(r[5] or 0),
            "entry_net_bp": round(float(r[6] or 0.0), 3),
            "note": "判据 D 的 markout 需行情库 ⇒ 用入场腿净额 + 止损腿数代理",
        }
    if key == "h527":
        # [h527 2026-09-29] **机制专属判据**：本改动只缩**规模**，不碰挂宽/闸门
        # ⇒ 必须把"事件数"与"名义额"两把尺子分开看：
        #   · 腿数（事件数）**不应下降**（甚至因敞口闸更晚触顶而略增）；
        #   · 单笔名义 P50 与尾部 USD 亏损应**等比缩小**。
        # 只看总体净/腿会把"规模变小"误读成"策略变差"，故必须逐币看。
        _watch = ["BNB", "NEAR", "ARB", "ENA"]   # 被缩规模的四币（R50 修订纳入 BNB：
        # BNB 是最大单一亏损源 −0.485$/h、t=−2.67，且占 48% 腿量 ⇒ 只能缩规模、不能删）
        _control = ["XRP"]                       # 对照：唯一正 bp/腿的币，本次**未改动**
        cur.execute(
            "SELECT symbol, count(*) AS legs,"
            " percentile_cont(0.5) WITHIN GROUP"
            "   (ORDER BY COALESCE(notional,0))::float8 AS p50,"
            " COALESCE(sum(net_bp*notional/1e4),0)::float8 AS net_usd,"
            " count(*) FILTER (WHERE meta_json->>'exit_path' LIKE 'stop_loss%%') AS stops,"
            " COALESCE(sum(net_bp*notional/1e4) FILTER ("
            "   WHERE meta_json->>'exit_path' LIKE 'stop_loss%%'),0)::float8 AS stop_usd"
            " FROM lane_ledger"
            " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
            "   AND symbol = ANY(%s)"
            " GROUP BY symbol ORDER BY symbol",
            (LANE, since, until, syms))
        coins: dict = {}
        for sym, legs, p50, net_usd, stops, stop_usd in cur.fetchall():
            coins[str(sym)] = {
                "legs": int(legs or 0),
                "notional_p50": round(float(p50 or 0.0), 2),
                "net_usd": round(float(net_usd or 0.0), 3),
                "stop_legs": int(stops or 0),
                "stop_usd": round(float(stop_usd or 0.0), 3),
            }
        out["per_coin"] = coins
        out["watch"] = _watch
        out["watch_legs"] = sum(coins.get(s, {}).get("legs", 0) for s in _watch)
        out["watch_stop_usd"] = round(
            sum(coins.get(s, {}).get("stop_usd", 0.0) for s in _watch), 3)
        _p50s = sorted(coins[s]["notional_p50"] for s in _watch
                       if s in coins and coins[s]["legs"] >= 5)
        out["watch_notional_p50_med"] = (
            round(_p50s[len(_p50s) // 2], 2) if _p50s else None)
        out["watch_stop_legs"] = sum(coins.get(s, {}).get("stop_legs", 0) for s in _watch)
        out["control"] = {s: coins.get(s, {}) for s in _control}
        out["control_legs"] = sum(coins.get(s, {}).get("legs", 0) for s in _control)
    return out


@_with_meta_lock
def do_judge(a, spec, key) -> int:
    import psycopg
    _out = ROOT / "research_l1" / "out" / f"{key}_verdict.json"
    if a.dry_run:
        _out = _out.with_name(f"{key}_verdict_dryrun.json")
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            meta = _load_meta(cur)
            trial = dict(meta.get(spec["meta_key"]) or {})
            since = trial.get("started_at")
            if not since:
                print(f"✗ meta.{spec['meta_key']}.started_at 缺失（试跑未部署）")
                return 1
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            hours = (dt.datetime.now(dt.timezone.utc)
                     - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
            if hours < MIN_JUDGE_HOURS and not a.force_rollback and not a.dry_run:
                print(f"✗ 试跑仅 {hours:.1f}h < {MIN_JUDGE_HOURS:.0f}h，未到判定窗口")
                return 2
            syms = trial.get("baseline_symbols") or \
                [str(s) for s in (meta.get("symbols") or []) if str(s)]
            t_legs = _per_leg(cur, since, now_iso, syms)
            # [h459 2026-09-29] **基线窗口 regime 对齐**：原基线 = 紧邻的 12h（T−12h → T），
            # 若 T 在下午，试跑窗跨"白天+夜"，基线窗跨"夜+晨" ⇒ 白夜颠倒、比较失真
            # （与 h458 的频率误杀同源）。改为**前一天同一钟点的 12h**（T−24h → T−12h）；
            # 若该窗无数据（新车道/断流）⇒ 退回紧邻 12h（旧行为）。
            _t0 = dt.datetime.fromisoformat(since)
            base_start = (_t0 - dt.timedelta(hours=24)).isoformat()
            base_end = (_t0 - dt.timedelta(hours=12)).isoformat()
            b_legs = _per_leg(cur, base_start, base_end, syms)
            if len(b_legs) >= 200:
                base_cut, base_win_h = base_start, BASELINE_HOURS
            else:
                base_cut, base_win_h = (_t0 - dt.timedelta(hours=BASELINE_HOURS)).isoformat(), BASELINE_HOURS
                b_legs = _per_leg(cur, base_cut, since, syms)
            w = _welch(t_legs, b_legs)
            tsum = _window(cur, since, now_iso, hours, syms)
            bsum = _window(cur, base_cut,
                           base_end if base_cut == base_start else since, base_win_h, syms)
            sub_t = _sub_stats(cur, key, since, now_iso, syms)
            sub_b = _sub_stats(cur, key, base_cut,
                               base_end if base_cut == base_start else since, syms)
            # [R54] **两半方向一致**（SPEC 里 criterion D 的口径，此前**没有实现**）：
            # 把试跑窗对半切、各算一次机制口径 ⇒ 若两半方向相反，说明"效应"是窗口内的
            # 时段偏差而非参数效应（h483 验证 ③ 的取值时正是靠这条，但那只在**研究阶段**
            # 手工做过，**判定阶段不会重做** ⇒ 判定产物里看不到它）。
            # 由 SPEC 的 `"two_halves": True` 打开；失败不影响判定（只记 error）。
            _th: dict = {}
            if spec.get("two_halves"):
                try:
                    _s0 = dt.datetime.fromisoformat(since)
                    _s1 = dt.datetime.fromisoformat(now_iso)
                    _mid = _s0 + (_s1 - _s0) / 2
                    _th = {
                        "mid": _mid.isoformat(),
                        "first_half": _sub_stats(cur, key, since, _mid.isoformat(), syms),
                        "second_half": _sub_stats(cur, key, _mid.isoformat(), now_iso, syms),
                    }
                except Exception as exc:  # noqa: BLE001
                    _th = {"error": f"{type(exc).__name__}: {str(exc)[:70]}"}

            # [h458 2026-09-29] **频率判据修正**：原判据 = 0.8×基线，而基线取自**部署时所在
            # 时段的活跃度**（如白天 223/h），试跑窗跨到夜间（65/h）就必然"崩塌" ⇒
            # 实测把 4 个有证据的功能（trail_lock / post_stop_decay / p1_hold / p45_hold）
            # 全部误杀 ✗。用户硬约束是**绝对** ≥60 腿/h ⇒ 以绝对地板为主判据，
            # 相对值仅作信息上报（why 里保留两者）。
            ABS_FLOOR = 60.0
            # [R34] **可交易时长口径**：外部停摆的小时不该算进频率分母。
            _base_until = base_end if base_cut == base_start else since
            _cov_t, _covr_t = _covered_hours(since, now_iso, syms)
            _cov_b, _covr_b = _covered_hours(base_cut, _base_until, syms)
            _tph_wall = float(tsum["legs_per_hour"])
            _bph_wall = float(bsum["legs_per_hour"])
            _tph = (tsum["legs"] / max(_cov_t, 0.01)) if _cov_t else _tph_wall
            _bph = (bsum["legs"] / max(_cov_b, 0.01)) if _cov_b else _bph_wall
            freq_abs_ok = _tph >= ABS_FLOOR
            freq_rel_ok = _tph >= FREQ_FLOOR * _bph
            freq_ok = bool(freq_abs_ok and freq_rel_ok)
            # 覆盖率安全阀：可交易时长不足窗口一半 ⇒ 拒绝下判决（样本不足，而非参数不好）
            _cov_guard = bool(_covr_t is not None and _covr_t < 0.5)
            if _cov_t is not None:
                print(f"[R34] 可交易时长：试跑 {_cov_t:.2f}h（覆盖 {_covr_t:.0%}）"
                      f"⇒ 腿速 {_tph:.1f}/h（墙钟口径 {_tph_wall:.1f}/h）；"
                      f"基线 {_cov_b if _cov_b is None else round(_cov_b, 2)}h"
                      f"⇒ {_bph:.1f}/h（墙钟 {_bph_wall:.1f}/h）")
            # [h484] 已知制度断点检查：窗口内若有与本次改动无关、但影响更大的变化，
            # 则**负向 Δ 不得直接判回滚**（降级 INCONCLUSIVE）。
            # [h521 2026-09-29] **改为自动检测**：不再依赖手工登记时间戳——
            # 凡注册表 `ops_changes` 里落在 [since, now] 的**其它**试跑部署/回滚，
            # 按定义都是本窗口的混淆源（手工登记只在"外部事件无审计"时才需要，如进程重启）。
            _breaks = []
            for _bk in (spec.get("known_regime_breaks") or []):
                try:
                    _bt = dt.datetime.fromisoformat(str(_bk))
                    if _bt.tzinfo is None:
                        _bt = _bt.replace(tzinfo=dt.timezone.utc)
                    if dt.datetime.fromisoformat(since) <= _bt <= dt.datetime.now(
                            dt.timezone.utc):
                        _breaks.append(str(_bk))
                except Exception:  # noqa: BLE001
                    continue
            _auto_breaks = []
            try:
                _since_dt = dt.datetime.fromisoformat(since)
                for _op in (meta.get("ops_changes") or []):
                    _a = str(_op.get("action") or "")
                    if _a.startswith(f"{key}_"):        # 本试跑自己的动作，跳过
                        continue
                    _ts = _op.get("ts")
                    if not _ts:
                        continue
                    try:
                        _t = dt.datetime.fromisoformat(str(_ts))
                    except Exception:  # noqa: BLE001
                        continue
                    if _t.tzinfo is None:
                        _t = _t.replace(tzinfo=dt.timezone.utc)
                    if _since_dt < _t <= dt.datetime.now(dt.timezone.utc) and \
                            ("deploy" in _a or "rollback" in _a or "restore" in _a):
                        _auto_breaks.append({"ts": _t.isoformat(), "action": _a})
            except Exception as exc:  # noqa: BLE001
                print(f"[h521] 自动断点检测失败（不影响判定）：{exc}")
            for _ab in _auto_breaks:
                if _ab["ts"] not in _breaks:
                    _breaks.append(_ab["ts"])
            if _auto_breaks:
                print(f"[h521] 窗口内检测到其它变更 {len(_auto_breaks)} 项 ⇒ "
                      f"计入已知断点：{[x['action'] for x in _auto_breaks]}")
            # [h500] 频率判据的**市场/波动调整**：绝对地板仍为主判据，但若
            # "市场明显更冷 或 波动明显更高"，则短fall 归因于外部 ⇒ 降级 INCONCLUSIVE。
            # 底线不变：腿速 < 0.4×地板（=24/h）仍无条件 ROLLBACK（引擎停摆/自锁）。
            _mkt_t = _market_activity(since, now_iso, syms) if not freq_abs_ok else {}
            _mkt_b = _market_activity(base_cut,
                                      base_end if base_cut == base_start else since,
                                      syms) if not freq_abs_ok else {}
            _mkt_cold = _mkt_hot = 0.0
            if _mkt_t and _mkt_b and _mkt_b.get("trades_per_h"):
                _mkt_cold = _mkt_t["trades_per_h"] / _mkt_b["trades_per_h"]
                if _mkt_b.get("vol_bp"):
                    _mkt_hot = _mkt_t["vol_bp"] / _mkt_b["vol_bp"]
            if a.force_rollback:
                verdict, why = "ROLLBACK", "manual_force_rollback"
            elif _cov_guard:
                # [R34] 可交易时长不足窗口一半 ⇒ 窗口里大半时间根本没数据，
                # 任何频率/净额对比都被外部停摆支配 ⇒ 只登记、不判死。
                verdict, why = "INCONCLUSIVE", (
                    f"insufficient_covered_time：窗口内仅 {_cov_t:.2f}h 可交易"
                    f"（覆盖 {_covr_t:.0%} < 50%）⇒ 外部停摆占主导，"
                    f"不以本窗口判该参数；墙钟腿速 {_tph_wall:.1f}/h、"
                    f"可交易口径 {_tph:.1f}/h")
            elif not freq_abs_ok and _tph < 0.4 * ABS_FLOOR:
                verdict = "ROLLBACK"
                why = (f"engine_stall {_tph:.1f}/h < "
                       f"{0.4*ABS_FLOOR:.0f}/h（远低于地板 ⇒ 引擎停摆/自锁，无条件回滚；"
                       f"墙钟口径 {_tph_wall:.1f}/h、可交易 {_cov_t if _cov_t is None else round(_cov_t,2)}h）")
            elif not freq_abs_ok and _mkt_t and _mkt_b and (
                    _mkt_cold < 0.85 or _mkt_hot > 1.2):
                verdict, why = "INCONCLUSIVE", (
                    f"freq_shortfall_market_explained {_tph:.1f}/h < "
                    f"{ABS_FLOOR:.0f}/h，但窗口内**市场更冷/波动更高**"
                    f"（真实逐笔 活跃度 {_mkt_cold:.2f}×、波动代理 {_mkt_hot:.2f}×）"
                    f"⇒ 短fall 归因于外部（风控闸在高波动里按设计收紧），"
                    f"**不据此回滚该参数**；需在同类市况下重开窗口再判")
            elif not freq_abs_ok:
                verdict = "ROLLBACK"
                why = (f"frequency_below_mandate {_tph:.1f}/h < "
                       f"{ABS_FLOOR:.0f}/h（用户硬约束；且市场活跃度/波动无异常："
                       f"{_mkt_cold:.2f}× / {_mkt_hot:.2f}×）")
            elif not freq_rel_ok:
                # 相对崩塌但绝对达标 ⇒ 不算回滚（跨 regime 基线的已知偏差），
                # 只登记为 INCONCLUSIVE 并在 why 里说明，避免误杀。
                verdict, why = "INCONCLUSIVE", (
                    f"freq_rel_low {_tph:.1f}/h < "
                    f"{FREQ_FLOOR}×{_bph:.1f}/h（跨 regime 基线，绝对达标 ⇒ 不判死）")
            elif w is None:
                verdict, why = "INCONCLUSIVE", "insufficient_samples"
            elif w["p"] <= ALPHA and w["delta"] > 0:
                verdict, why = "PASS", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            elif w["p"] <= ALPHA and w["delta"] < 0 and _breaks:
                verdict, why = "INCONCLUSIVE", (
                    f"confounded_window：Welch p={w['p']:.3f} delta={w['delta']:+.3f}bp 为负，"
                    f"但试跑窗内含已知制度断点 {_breaks}（与本次改动无关、影响更大）"
                    f"⇒ 该对比不具因果性，**不据此回滚**（需要人工裁决或另开干净窗口）")
            elif w["p"] <= ALPHA and w["delta"] < 0:
                verdict, why = "ROLLBACK", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            else:
                verdict, why = "INCONCLUSIVE", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"

            meta = _append_ops(meta, {"ts": now_iso,
                                      "action": f"{key}_judge_{verdict.lower()}",
                                      "note": why, "trial": tsum, "baseline": bsum,
                                      "welch": w, "sub": {"trial": sub_t,
                                                          "baseline": sub_b},
                                      # [R34] 可交易时长口径（外部停摆不计入频率分母）
                                      "covered": {"trial_h": (None if _cov_t is None
                                                              else round(_cov_t, 3)),
                                                  "trial_ratio": (None if _covr_t is None
                                                                  else round(_covr_t, 4)),
                                                  "baseline_h": (None if _cov_b is None
                                                                 else round(_cov_b, 3)),
                                                  "legs_per_hour_covered": round(_tph, 2),
                                                  "legs_per_hour_wall": round(_tph_wall, 2)}})
            pname = spec["field"].split(".")[-1]
            fields = [(f.split(".")[-1], _coerce_param(t), _coerce_param(rb))
                      for f, t, rb in (spec.get("fields") or
                                       [(spec["field"], spec["to"], spec["rollback_to"])])]
            if verdict == "ROLLBACK":
                params = dict(meta.get("params") or {})
                old = {n: _current_of(params, n, 0.0) for n, *_ in fields}
                for n, _t, rb in fields:
                    params[n] = rb
                meta["params"] = params
                # [h433] 判定回滚也不重置 stats_since（账户级口径不动）
                meta[spec["meta_key"]] = {**trial, "verdict": verdict,
                                          "rolled_back_at": now_iso, "why": why,
                                          "baseline_symbols": syms}
                meta = _append_ops(meta, {
                    "ts": now_iso, "action": f"{key}_rollback",
                    "field": spec["field"], "from": old,
                    "to": {n: rb for n, _, rb in fields},
                    "note": f"12h 判定：{why}"})
            else:
                # [R74] INCONCLUSIVE ⇒ 窗口**延长**并重排判定。此前只写 `extend_until`、
                # **不更新 `judge_at`** ⇒ 而链（`h464_chain`）的"有界等待"与"改期对准下次
                # 判定"都读 `judge_at` ⇒ 永远看到 12h、且改期目标恒过期 ⇒ 安全网失效。
                # 现让 `judge_at` 表示"**本试跑下次判定的时刻**"（与 `extend_until` 同步），
                # 两侧语义一致；链侧也已做成取两者较晚者的防御式实现。
                _ext_iso = ((dt.datetime.now(dt.timezone.utc)
                             + dt.timedelta(hours=EXTEND_HOURS)).isoformat()
                            if verdict == "INCONCLUSIVE" else None)
                meta[spec["meta_key"]] = {**trial, "verdict": verdict,
                                          "judged_at": now_iso, "why": why,
                                          "baseline_symbols": syms,
                                          "extend_until": _ext_iso,
                                          **({"judge_at": _ext_iso} if _ext_iso else {})}
            if not a.dry_run:
                _save(cur, c, meta)
            else:
                print(f"[dry-run] 不落库。若实际执行：verdict={verdict} "
                      f"{pname} 将{'回滚' if verdict == 'ROLLBACK' else '保持'}")

    res = {"trial": key, "verdict": verdict, "why": why, "judged_at": now_iso,
           "hours": hours, "trial": tsum, "baseline": bsum, "welch": w,
           "freq_ok": freq_ok, "sub": {"trial": sub_t, "baseline": sub_b},
           # [R54] 两半方向一致（SPEC 里 criterion D）——由 SPEC 的 two_halves 打开
           "two_halves": _th,
           "breaks_in_window": _breaks if "_breaks" in dir() else [],
           # [R73] **可交易时长口径**必须进产物：它是 `freq_ok` 的判据基础，而产物里的
           # `legs_per_hour` 是**墙钟**口径 ⇒ 两者在停摆窗里会差一倍以上（示例：38.6 vs 62.6）。
           # 此前只写进登记表 `ops_changes`，验收记录（`h546`）读不到 ⇒ 恒显示 `{}` ✗。
           "covered": {"trial_h": (None if _cov_t is None else round(_cov_t, 3)),
                       "trial_ratio": (None if _covr_t is None else round(_covr_t, 4)),
                       "baseline_h": (None if _cov_b is None else round(_cov_b, 3)),
                       "legs_per_hour_covered": round(_tph, 2),
                       "baseline_legs_per_hour_covered": round(_bph, 2),
                       "legs_per_hour_wall": round(_tph_wall, 2),
                       "baseline_legs_per_hour_wall": round(_bph_wall, 2)},
           "dry_run": bool(a.dry_run)}
    _out.parent.mkdir(parents=True, exist_ok=True)
    _out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"判定已写 {_out}")

    if verdict == "INCONCLUSIVE" and not a.dry_run:
        _nxt = dt.datetime.now() + dt.timedelta(hours=EXTEND_HOURS)
        rc = _schedule_judge(spec["task"], "h425_repair_trial.py", key, _nxt)
        print(f"INCONCLUSIVE ⇒ 已排 +{EXTEND_HOURS:.0f}h 再判定 @ {_nxt:%Y-%m-%d %H:%M} rc={rc}")
    return 0


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="H425-427 出场架构修复试跑")
    ap.add_argument("--trial", required=True, choices=sorted(SPECS))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deploy", action="store_true")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--judge", action="store_true")
    ap.add_argument("--force", action="store_true", help="部署：绕过守卫（记审计）")
    ap.add_argument("--force-rollback", action="store_true", help="判定：强制回滚")
    ap.add_argument("--dry-run", action="store_true", help="判定：预演不落库")
    a = ap.parse_args()
    spec = SPECS[a.trial]
    if a.deploy:
        return do_deploy(a, spec, a.trial)
    if a.rollback:
        return do_rollback(a, spec, a.trial)
    return do_judge(a, spec, a.trial)


if __name__ == "__main__":
    raise SystemExit(main())
