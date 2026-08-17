# V7 正式上线报告

## 结论

V7 自进化因子工厂已接入实机生产调度，状态为**可正式运行**：

- 三周期因子进化：`4h(L)`、`15m(M)`、`5m(S)` 三条独立池，复用现有 GP + MCTS + Codegen LLM + WFO/DSR/PBO；
- 长期记忆：每轮进化结束自动沉淀，下一轮 Codegen 自动检索注入；
- 不过度门禁：记忆只影响“下一轮生成什么假设”，不参与本轮晋升/淘汰，现有数学硬门禁全部保留；
- 亏损变学费：全拒/衰退/链路中断都会成为下一轮假设输入，而不是黑名单；
- 2080Ti 与当前 1070 驱动共享，换卡即可，代码无需改动。

## 上线内容

1. `backend/services/evolution/evolution_memory_v7.py`
   - SQLite 长期记忆：教训、代际报告、检索日志、使用计数、质量分、自动退役；
2. `backend/services/evolution/evolution_v7_runner.py`
   - 三周期手动/脚本执行器，复用 `run_factor_evolution_loop`；
3. `backend/services/evolution/factor_evolution_loop.py`
   - 每轮 finally 自动写记忆；
   - Codegen prompt 注入历史教训/成功配方；
   - 新增 `run_mid_factor_evolution_loop`、`run_v7_memory_maintenance`；
4. `backend/main.py`
   - 生产调度：03:00 4h、04:00 5m、06:00 15m、06:50 记忆维护、每小时权重；
5. `backend/api/evolution_routes.py`
   - `GET /api/evolution/v7-memory`
   - `POST /api/evolution/v7-memory/maintenance`
6. 启动脚本：
   - `RUN_V7_CHECK.bat`
   - `RUN_V7_QUICK.bat`
   - `RUN_V7_FULL.bat`
   - `RUN_V7_LAUNCH.bat`（正式上线入口）

## 正式上线操作

```bat
RUN_V7_LAUNCH.bat
```

该脚本会：
1. 执行 Python 语法检查 + V7 接线检查；
2. 重启后端，使三周期生产调度生效；
3. 后端启动 45 秒后自动补一轮 4h quick（若 6 小时内未跑过）。

之后无需人工执行：每日 03:00 4h、04:00 5m、06:00 15m 自动运行。
如需“现在立即”跑完整三周期：`set V7_LAUNCH_FULL_NOW=1` 后再执行。

## 开机自启

已在 Windows 启动文件夹创建：
`C:\Users\heida\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\V7_AlphaArena_Backend_AutoStart.bat`

下次登录 Windows 时自动检查后端健康，未运行则自动启动后端；后端启动后 V7 调度与自动 quick 进化全部自动运行，不需要人工点脚本。

## 上线后观测

```bat
REM 记忆库状态
backend\.venv\Scripts\python.exe -m backend.services.evolution.evolution_v7_runner memory

REM API
curl http://127.0.0.1:8000/api/evolution/v7-memory
```

## 回滚方式

- 关闭 V7 记忆写入：设置 `V7_MEMORY_ENABLED=0`，本轮门禁与因子进化行为完全不变；
- 关闭 Codegen 注入：设置 `FACTOR_CODEGEN_ENABLED=0`，GP/MCTS 仍运行；
- 删除记忆库即可冷启动：`backend\data\factor_evolution_memory_v7.db`；
- 调度回滚：从 `backend/main.py` 中移除 `factor_evolution_mid_15m_daily_v7` 和 `v7_factor_memory_maintenance_daily` 两个注册块即可。
