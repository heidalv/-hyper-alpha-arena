# V7 自进化因子工厂 —— 实机落地说明（当前即可运行）

> 状态：已接入 `D:\001Alpha\Hyper-Alpha-Arena` 实机仓库。
> 原则不变：不过度门禁；LLM 只生成/批评/总结，不评价 alpha；亏损先尸检再下架，前期亏损是学费。

## 1. 本轮新增（实机文件）

| 文件 | 作用 |
|---|---|
| `backend/services/evolution/evolution_memory_v7.py` | 长期记忆 DB（SQLite，`backend/data/factor_evolution_memory_v7.db`）：教训提取、质量分、使用计数、词向量相似检索、代际报告 |
| `backend/services/evolution/evolution_v7_runner.py` | 三周期执行器：复用现有 `run_factor_evolution_loop`，每轮结束自动写记忆 |
| `backend/services/evolution/factor_evolution_loop.py` | **已接线**：Codegen prompt 自动注入 V7 历史教训/成功配方（只注入假设，不注入结论） |
| `RUN_V7_CHECK.bat` | 接线自检（含 py_compile 语法检查） |
| `RUN_V7_QUICK.bat` | 快速闭环：4h/15m/5m，只跑种子/模板，先证明闭环能转 |
| `RUN_V7_FULL.bat` | 完整闭环：GP + MCTS + Codegen LLM + WFO/DSR/PBO |
| `RUN_V7_LAUNCH.bat` | **正式上线**：自检 → 重启后端 → 注册每日调度 → 立即后台启动三周期完整进化 |

## 2. 正式上线步骤

```bat
REM 一条命令正式上线（之后无需人工执行）：
REM   自检 -> 重启后端 -> 自动注册三周期每日调度 + 启动后自动补 4h quick
RUN_V7_LAUNCH.bat

REM 如需“现在立即”跑完整三周期，再加环境变量：
set V7_LAUNCH_FULL_NOW=1
RUN_V7_LAUNCH.bat

REM 只想验证闭环，不重启后端：
RUN_V7_QUICK.bat

REM 查看记忆
backend\.venv\Scripts\python.exe -m backend.services.evolution.evolution_v7_runner memory
```

正式上线后，后端 `main.py` 已注册每日调度：

| 时间 | 任务 |
|---|---|
| 03:00 | 4h 大周期完整进化（L） |
| 04:00 | 5m 小周期完整进化（S，原有） |
| 06:00 | 15m 中周期完整进化（M，V7 新增） |
| 06:50 | V7 长期记忆维护 |
| 每小时 | 在线权重更新 |

观测 API：
- `GET /api/evolution/v7-memory`：V7 记忆统计与最近教训
- `POST /api/evolution/v7-memory/maintenance`：手动触发记忆维护

## 3. 三周期映射

| V7 周期 | 现有进化 period | 职责 |
|---|---|---|
| L 大周期 | 4h（可扩 8h/1d） | 方向背景、趋势、资金费率/OI 周期 |
| M 中周期 | 15m（可扩 30m/1h） | 结构择时、CVD 背离、突破/吸收 |
| S 小周期 | 5m（可扩 1m） | 触发执行、盘口失衡、taker 主动量 |

三周期共振不设硬 veto：大周期相反只降仓，最终仓位由现有物理安全网决定。

## 4. 记忆如何反哺（越用越聪明）

```
run_factor_evolution_loop(period)
        ↓
report(硬指标: candidates/evaluated/survivors/promoted/degraded)
        ↓
record_report() → v7_lessons
   - 晋升因子 → success_recipe（质量 0.95）
   - 0 晋升但有候选 → gate_lesson（下一轮低换手/高 ICIR/互补假设）
   - 衰退因子 → decay_case（避开同窗口同字段）
        ↓
下一轮 factor_evolution_loop Codegen:
   build_codegen_context(period) → 检索 top-8 教训
   → 注入 Codegen prompt（LLM 只看到“历史硬指标事实”）
```

检索排序 = 词向量相似度 + 0.5×质量分 + 0.15×ln(1+使用次数) + 时间衰减。
记忆有使用计数与状态位；无效教训可 `status='retired'`，不会无限膨胀。

## 5. 与现有门禁的关系（不过度门禁）

- 硬门：现有 `audit` 防未来函数、三段切分、WFO、DSR/PBO、测试集复评、容量/换手。这些是数学安全，不动。
- 记忆门：V7 只改 Codegen prompt 的“假设输入”，不参与任何晋升/淘汰判定。
- 亏损：现有 PAPER/SMALL_LIVE 状态机已允许亏损换样本；V7 把每轮“全拒/衰退/晋升”都沉淀为下一轮假设，不是把亏损因子直接拉黑。

## 6. 常见问题

- `already_running`：现有因子进化已在跑，等它结束或查看 `evo_runtime.snapshot()`。
- 数据深度不足：现有循环会 nudge 回填并中止；先让深度回填完成，再跑对应周期。
- `FACTOR_CODEGEN_ENABLED=0` 或未配 `factor_mining` LLM：Codegen 显式降级，GP/MCTS 仍跑，记忆仍写入；LLM 配好后下一轮自动生效。
- 2080Ti 与 1070 共享驱动：直接换卡即可；embedding/RAG 现有 `rag_knowledge_service` 会自动切 CUDA，V7 记忆检索不依赖 GPU。
