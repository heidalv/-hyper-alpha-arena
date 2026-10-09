# -*- coding: utf-8 -*-
"""[h818] 审计记录:Cursor 的高频交易改造全面检查。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

## 10. 审计:Cursor 的高频改造是否完全到位(10-04 18:xx)

### 10.1 内核已经不是做市逻辑(逐项核实 ✓)

| 项 | 证据 |
|---|---|
| 单边挂单,不双边做市 | active_flow 只在 model_side 一侧挂 quote_bid/quote_ask 之一 |
| 挂单 0 费、只有危险/灾难吃单 | `_apply(..., fee_bp=0)` 常态;taker 仅 `taker_risk`/`taker_stop` |
| 成交必须真被打到 | `_maker_touched(...)` 校验区间低/高价;否则不入账 |
| 删掉固定止盈 | active_flow `del ... sl_bp, tp_bp`;止损 = 2×300s 波动夹 15~40 |
| mu 驱动进场 | `model_mu > entry_margin_bp` 才挂;方向取 model_side |
| 离场阶梯 | `choose_exit`:no_book→taker_risk→taker_stop→maker_time→maker_edge→hold |
| 报错不退回做市 | runner 捕获后 `dec.skip="active_flow_error"` 且清空买卖价 |
| 逐仓名义(不读做市参数) | `notional_cap_usd`(0.5%/笔、同向 2%)+ `exchange_leverage`(强平≥3×止损) |
| 做市进化冻结 | evolution.py:253 `active_flow_mode>0 ⇒ frozen_mm_grid` |
| DSH 白名单换成流参数 | `FLOW_SAFE_PARAMS` + `SYSTEM_PROMPT_FLOW` + metric=roundtrip_y + era=flow |
| 旧定律不进提示 | `playbook_for_prompt`/`experience_for_prompt` 只留 era=flow |
| 快判改往返三关 | `should_rollback_flow`(平均 y≥改前 80%、吃单费占比不升、成交率不塌、强平=0) |

**验证手段**:单测 10/10 通过;冒烟测试(重启后持仓)⇒ 走 `maker_time` 挂减仓单、
未抛错;全量编译通过。

### 10.2 **阻断级缺口(已由本轮补上)**:门的生产者不存在

flow_rules 只有**规则**+ runner 是**消费者**,但没有任何脚本按新协议
(60/20/20 + 隔离带 + 挂单可执行标签 + 分档 OOS 表)生产门文件。
线上 `data/flow_gate_last.json` 还是旧格式(allow/pred_bp)
⇒ `gate_decision` 恒返回 `no_oos` ⇒ **新引擎永久关门**(安全但永不交易)。
实测:`gate_decision(NEAR) ⇒ allow=False reason=no_oos`。

**已补**:`scripts/h817_flow_trainer.py`(每 20 分钟,任务 DSH_MM_FLOW_TRAIN):
- 特征用 flow_rules.assemble_features 的 15 维(金额失衡 5/15/60/300s、合成K线、
  深度失衡、BTC 相对、宽度);
- 标签 = **挂单可执行路径**(买一进/卖一出,进场与出场都必须被成交真的打到,
  手续费 0);未打到不计入;
- 切分 60% / 隔离 H / 20% 校准 / 隔离 H / 20% 考试;校准段出分位边界,考试段报 OOS;
- 写新格式门:`{oos:{mean_y,n_eff,win_rate}, deciles, mu, side, allow, max_hold_sec}`;
- ⚠️ 修掉一个偷看未来的错:两个方向**各训一个模型**(原来是取事后更好的方向当标签)。

**首轮结果(12h)**:0/8 开门 —— 在"挂单可执行 + 真被打到 + 分档 OOS"这套严格
口径下,当前数据没有任何正期望档位(此前中点口径下的 BTW/LYN 优势在严格口径下消失)。

### 10.3 还没改到的三处

| # | 位置 | 说明 |
|---|---|---|
| C2 | 线上特征层 | trainer 从 DB 算 15 维;runner 只把 mu/side 传给 active_flow ⇒ "线上线下同一套"目前只在离线成立 |
| C3 | live_bridge.py:78/241/248 | 实盘杠杆仍硬编码 2.0(做市遗留);文档要求按 `exchange_leverage(stop_bp)` 逐笔设置 |
| C4 | (已在生产者侧补) | 文档要求"分数落在正期望档位";gate_decision 原只校验币级 OOS ⇒ h817 在生产者侧加了 `mu ≥ bucket_lb` |

### 10.4 现状
新引擎已在运行(worker 已重启加载新码):skip = `model_gate_no_edge` 100%、
fills=0 —— **没有正期望档位就不交易**,这正是文档要求的默认状态。
完整链路:h817 生产门 → runner 消费 → active_flow 单边挂单/离场阶梯
→ flow_roundtrip_log.jsonl → self_tuner_verdict 三关快判。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h818 audit")
