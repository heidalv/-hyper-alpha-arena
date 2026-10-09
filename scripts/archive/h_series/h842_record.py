# -*- coding: utf-8 -*-
"""[h842] 记录:体检 + 性能调研结论。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

## 15. 体检 + 性能调研(10-04 23:4x)

### 15.1 体检:运行正常 ✓
```
worker pid 25996 存活 | 状态文件 1s 前更新 | ticks 递增
33 币 | fills/h = 130.6 | 挂单 one=26879 / **both=0**(全单侧 ✓)
近 15 分钟:34 腿 **+6.227U** 每腿 +12.48bp
近 60 分钟:55 腿 **+5.791U** 每腿 +7.07bp
近 30 分钟路径:flow_entry_maker 23 腿 +3.443U | taker_stop 19 腿 −19.231U
```
⇒ 探索模式(火力全开)已生效并盈利;唯一失血口仍是灾难止损(19 腿 −19.2U)。

### 15.2 性能调研(CPU 不是瓶颈,DB/IO 是)
| 测项 | 实测 | 结论 |
|---|---|---|
| `plan_tick` 单币纯决策 | **0.12 ms** | 33 币 = 4ms/tick,**CPU 完全不是瓶颈** |
| worker 实测节拍 | 1383→1633 ms/tick | = 1.0s 配置睡眠 + **约 630ms 工作** |
| 单币逐笔查询(60s 窗口)| 0.6 ms/币 → 21 ms/tick | 也不是瓶颈 |
| 批量版(ANY 一条查询)| 1.3 ms(6× 提速)| 只能省 ~20ms |
| 情况表+门文件解析 | 2.2 ms → **73 ms/tick** | 可缓存(约 10%)|
| **建连开销** | **87 ms/次**(市场库/账本库)| 若每 tick 建连 ⇒ 是主要嫌疑 |
| worker CPU 占比 | 259s CPU / 1680s 墙钟 = **15%** | 85% 在等 I/O |

### 15.3 已修的最大单点:缺失索引(543× 提速)
```
asterdex_trades:1086 MB / 2.2M 行
索引只有 pkey(id) 和 (symbol, event_ts_ms) ⇒ **缺 (event_ts_ms)**
⇒ 任何"按时间窗扫全市场"的查询都全表扫:实测 406 ms/次
已建:CREATE INDEX ix_adx_trades_ts ON asterdex_trades (event_ts_ms)  (3.1s 建完)
⇒ 406 ms → **1 ms(543×)**
受益者:h817 训练器 / h828 / h831 / h832 等**所有按时间窗扫成交的离线任务**;
另已 VACUUM ANALYZE(清死元组)。
注:对 tick 本身影响小(它走 (symbol, event_ts_ms) 索引,0.6ms/币)。
```

### 15.4 提速路线(按收益排序,待执行)
| # | 动作 | 预期收益 | 风险 |
|---|---|---|---|
| 1 | ✅ 建 (event_ts_ms) 索引 | 离线任务 543× | 无(已做) |
| 2 | 情况表/门文件按 mtime 每 tick 只读一次 | ~70 ms/tick(10%) | 低 |
| 3 | 33 币状态保存合并成一条 UPSERT | 估 100~300 ms/tick | 低 |
| 4 | 复用 DB 连接(pool/持久连接),避免 87ms 建连 | 若每 tick 建连 ⇒ 最多 ~500 ms | 中 |
| 5 | 逐币成交查询合成一条 ANY(已验 6×) | ~20 ms/tick | 低 |
| 6 | 上面都做完后把 `MM_LANE_WORKER_INTERVAL_SEC` 1.0 → 0.3~0.5s | 决策节拍 2~3× | 低 |

**优先级建议**:先加"逐段计时探针"(在 tick 里打点:fetch_market / 决策 / 落库 /
状态写)确认那 630ms 的构成,再动手 —— 避免优化了不痛的地方。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h842")
