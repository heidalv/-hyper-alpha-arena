# -*- coding: utf-8 -*-
"""[h849] 记录:第一次真正的参数试跑(era=flow,pending 已登记)。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

## 16. 第一次真正的参数试跑(pending,10-05 00:28)

**数据依据(桥 23:58)**:
- 往返 49 条,近 1h 43 条 ≥ 30 门槛(可裁决);
- 平均 y **+13.11bp**,强平 0;经验层三档全转正(+6.7 / +14.9 / +50.7bp);
- **唯一失血口 = 灾难止损**:taker_stop −21~−57bp,吃单费占比 85%;
  而 0 费挂单往返 +2~+106bp。

**动作(严格单变量)**:`disaster_stop_cap_bp` **40 → 60**(floor 保持 15 不动)
写入 `data/flow_learn_params.json`;rollback = 40。

**登记**:`logs/self_tuner_pending.json` 追加一条
`{era: "flow", param: disaster_stop_cap_bp, old: 40, new: 60, rollback: 40,
 verdict_metric: "roundtrip_y", verdict_at: +30min, verdict_rule: should_rollback_flow}`。

**试跑前基线**:n=51,平均 y **+9.85bp**,吃单费占比 79%,挂单成交率 53%,强平 0。
**裁定时间**:00:58:48(三关:平均 y ≥ 改前 80%、吃单费占比不升、
挂单成交率不塌、强平=0)。

**机制注意**:`load_learn_params` 每次调用都读文件(无缓存)⇒ worker 下一 tick 即生效;
`self_tuner_verdict.py` 的 flow 分支读 `flow_roundtrip_log.jsonl` + `window_stats`
+ `should_rollback_flow`(已审计确认接线完整)。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h849")
