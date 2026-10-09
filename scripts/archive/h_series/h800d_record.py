# -*- coding: utf-8 -*-
"""[h800d] 执行记录:主动流交易模式上线(修 NameError 后)。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六bv、主动流交易模式上线(h800,10-04 15:0x)

用户:"腿速不够 + 还是老的做市对冲思想。好好的想想"。
⇒ 新模块 `backend/services/market_maker/active_flow.py` + 车道参数
`active_flow_mode=1`:**不再挂被动单、不囤仓、不对冲**。

**语义(30s-5min 超短交易的原始设计)**:
- 空仓 + |OFI|≥0.3 且与 300s 趋势同向 ⇒ **市价(taker)进场**(真实盘口价,
  没有队列份额假设、没有填充概率);
- 持仓:只等离场 —— SL −40 / TP +60 / 流翻转 / 5 分钟硬顶,期间不挂任何单;
- 全部成交 = taker 真实价。

**部署过程中的两个坑(均已修)**:
① `active_flow_mode` 字段没加进 LaneRiskLimits 数据类 ⇒ getattr 恒 0,
  模式没生效(加字段修复);
② 分支放在了 `local_book` 初始化**之前** ⇒ NameError 被裸 except 吞掉,
  半小时静默跑旧逻辑(移到初始化之后 + 加异常日志修复)。

**实测(15:0x)**:side_counts both=0(被动挂单彻底消失 ✓);
skip 分布 = no_flow 1223 / holding 238 / flow_vs_trend 85(模式的语义在动);
成交 = `flow_entry_taker`(NEAR/CBRS)✓。

**下一轮**:流阈值 0.3 仍偏严(79% tick 无流)⇒ 量 OFI 分布校准到
"有流就进、无流就等"的档位,腿速目标显著高于旧做市上限(11 笔/币/时)。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h800d")
