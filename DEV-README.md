# Heidalv-Alpha-Arena 使用指南（就看这一页）

> 之前有 40+ 个启动脚本把人绕晕了。现在清理到 **4 个入口**，双击即用。

## 一、两种运行模式任选其一

### A. 浏览器开发模式（日常改代码用这个）

```
双击 dev-start.bat        → 启动 uvicorn:8000 + vite:5173
浏览器打开 http://localhost:5173
改完代码要关机前：
双击 dev-stop.bat         → 彻底停干净
```

看当前状态：`dev-status.bat`（进程 PID / 端口 / 日志路径一屏显示）

**特点**：
- 前端 HMR 热更新，改 `.tsx` 立即刷新
- 后端 uvicorn 加 `--log-level info`，日志写 `logs\backend.log`
- 前端日志写 `logs\frontend.log`
- 子进程独立跑，**关闭 Cursor 终端不会杀进程**；重启前先 `dev-stop.bat`

### B. 桌面 App 模式（打包后交付给别人用）

```
双击 启动AlphaArena.bat    → 自动 npm run build 打包前端 + 弹 pywebview 窗口
```

**特点**：
- 一键安装缺失依赖（pywebview / pystray / Pillow）
- 前端产物部署到 `backend\static`，uvicorn 自己提供静态文件
- 桌面窗口关闭 = 最小化到托盘（托盘菜单可退出）
- 日志：`logs\launcher.log`

## 二、目录说明

```
Hyper-Alpha-Arena/
├── dev-start.bat           ← 浏览器开发：启
├── dev-stop.bat            ← 浏览器开发：停
├── dev-status.bat          ← 查看状态
├── 启动AlphaArena.bat       ← 桌面 App：启（pywebview 窗口）
│
├── scripts/
│   ├── start-dev.ps1       ← dev-start.bat 的真正实现
│   ├── stop-dev.ps1        ← dev-stop.bat 的真正实现
│   └── status-dev.ps1      ← dev-status.bat 的真正实现
│
├── logs/                   ← 运行日志（backend.log / frontend.log / launcher.log）
├── data/alpha_arena.db     ← SQLite 主数据库
│
├── backend/                ← FastAPI + SQLAlchemy
├── frontend/               ← Vite + React
├── desktop/launcher.py     ← 桌面 App 主入口（被 启动AlphaArena.bat 调用）
│
└── _archive/               ← 归档目录，40+ 个历史脚本 + 1GB PostgreSQL 本地包
    ├── launchers/README.md ← 归档分类说明
    └── postgresql/         ← 旧的本地 PG（已不使用，改用 SQLite）

decisions_p4.md             ← P4 规划（因子落地 + 提示词进化修复），正文在根目录，避免 IDE 打不开深层路径
```

### P4 规划文档打不开的解决办法

若从聊天里的链接打开 `docs\research\decisions_p4.md` 报错 **Unable to resolve resource**：

1. 在 Cursor 按 **Ctrl+P**，输入 `decisions_p4`，选择 **`Hyper-Alpha-Arena/decisions_p4.md`**（仓库根目录这一份）。
2. 或在 Windows 资源管理器中打开：`Hyper-Alpha-Arena\decisions_p4.md`。
3. `docs\research\decisions_p4.md` 仅保留跳转说明；**以根目录 `decisions_p4.md` 为准**。

## 三、常见场景

### 热重载（Hot Reload）工作方式

两端都带热重载，**改代码保存即可，不用重启整个 dev-stack**。

| 层 | 工具 | 触发条件 | 响应时间 |
| --- | --- | --- | --- |
| 前端 TSX/CSS | Vite HMR | 保存 `frontend/app/**` 任何文件 | 通常 < 300ms，浏览器原地刷新组件 |
| 后端 Python | `uvicorn --reload` + `watchfiles` | 保存 `backend/**/*.py` | 10-30s（整个 app 重启，跑 scheduler/DRL/RAG init） |

**后端 reload 有哪些注意事项**：

- 只监控 `backend/` 目录（通过 `--reload-dir backend`），**`data/*.db`、`logs/*.log`、`_archive/*` 的变化不会触发误重启**。
- reload 会**整个应用重启**：所有 `APScheduler` 任务、WebSocket 连接、DRL 模型内存状态全部归零。如果当前正在跑长 DRL 训练或想让 scheduler 连续运行，用 `-NoReload` 关掉。
  ```
  powershell -File scripts\start-dev.ps1 -NoReload
  ```
- reload 后的进程树是 **reloader 主进程 → spawn_main 子 worker** 两层。`dev-stop.bat` 已能**两阶段全灭**（先杀子 worker 再杀 reloader），不会留孤儿进程占端口。
- 如果保存 `.py` 有 `SyntaxError`，uvicorn 会**保持旧 worker 存活**，日志写 `WARNING: WatchFiles detected changes ... SyntaxError`，修好语法并再次保存会触发二次 reload。

**前端 HMR 有哪些注意事项**：

- `vite.config.ts` 已 `strictPort: true`：5173 被占就报错退出，绝不静默漂 5174/5175，避免浏览器书签连错旧实例。
- `watch.usePolling = false`：用 chokidar 原生事件（`ReadDirectoryChangesW`），CPU 占用显著低于以前的 300ms 轮询。
- `watch.ignored` 排除了 `backend/**`、`data/**`、`logs/**`、`_archive/**`、`*.db`、`*.log`、`*.sqlite`、`node_modules/**`，后端 db 写入不会骚扰前端 watcher。
- HMR 失败（浏览器控制台红色报错、编辑器 overlay）99% 是 `.tsx` 有编译错误，修好就刷新。真不行 `Ctrl+Shift+R` 硬刷一次。

### 前端看到的是旧代码？

99% 是因为 vite 没跑或者跑在了别的端口。

```
dev-stop.bat        ← 确保清干净
dev-start.bat       ← 重启
dev-status.bat      ← 确认 port 5173 有监听
```

`vite.config.ts` 已设 `strictPort: true`，被占就报错退出，不会静默漂到 5174。

### 端口被占用？

```
dev-stop.bat   ← 它会扫描 5173/5174/5175/8000/8001 并精准释放
```

### 想用 `:8001` 跑第二个后端做 A/B 对比？

```
powershell -File scripts\start-dev.ps1 -BackendPort 8001 -NoFrontend
```

### 只重启后端不碰前端？

```
dev-stop.bat    ← 会停全部，但 5 秒内 vite 会被 dev-start 重启
dev-start.bat -NoFrontend   ← 不会碰到 vite（但当前 bat 不支持参数传递，直接用 ps1）
powershell -File scripts\start-dev.ps1 -NoFrontend
```

### 看后端最新日志

```
powershell -File - <<"EOF"
Get-Content logs\backend.log -Tail 50 -Wait
EOF
```

或者直接 VS Code/Cursor 里打开 `logs\backend.log`。

## 四、⚠ 重要提醒

以下旧脚本**不要再用**（已归档到 `_archive/launchers/`）：

| 旧脚本 | 为什么有害 |
| --- | --- |
| `Stop-All.bat` / `stop.ps1` | `Stop-Process -Name node -Force` 会杀 Cursor 自己的 TS server / Pyright，导致 IDE 崩溃假死 |
| `Start-All.bat` | 要求 `backend\venv` + 本地 PostgreSQL，两者都没有，直接报错 |
| `Start-Dev.bat`（Docker 版） | `docker-compose.dev.yml` 已删除（2026-06-21 移除 Docker 依赖），不再需要 |
| 各种 `start-dev-*.bat` | 启动写法各异，停不干净，切错端口 |

## 五、出问题排查路径

1. `dev-status.bat` 看当前状态（80% 问题是端口没放）
2. 打开 `logs\backend.log` 尾部，有没有 `ERROR`
3. 打开 `logs\frontend.log` 尾部，Vite 有没有报错
4. 还不行：`dev-stop.bat` → `dev-start.bat` 从零重来
5. 再不行：把 `logs\*.log` 发给 AI 助手看
