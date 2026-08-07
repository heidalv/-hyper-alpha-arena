# 快速启动指南

## 推荐：独立桌面端（不依赖系统浏览器）

```
双击：DESKTOP.bat
```

或：

```powershell
# 后端 :8000 已启动时
cd frontend-next
npm run electron:dev
```

1. 弹出 Electron 窗口（登录页）
2. 用管理员账号登录（邮箱 `heidalv@outlook.com`），或注册普通账号
3. 进入交易终端

### 账号与数据隔离（简要）

- 每个登录用户 = 一个租户；交易/持仓/配置等按 `tenant_id` + PostgreSQL RLS 隔离
- **管理员**（`role=admin` + `tier=vip`）：可访问 `/api/admin/*`，可跨租户查看，并可为他人分配：
  - 等级：`PATCH /api/admin/users/{id}/tier` → `free` / `pro` / `vip`
  - 角色：`PATCH /api/admin/users/{id}/role` → `user` / `admin`
  - 启停：`PATCH /api/admin/users/{id}/status`
- 本地旁路 `AUTH_LOCAL_TENANT` 已关闭；未登录写操作会 401
- 提升管理员脚本（不落盘密码）：`python -m backend.scripts.ensure_admin_user`（需 `ADMIN_EMAIL` / `ADMIN_PASSWORD`）

**重要（别混淆）**：
- 每个账户要**自己配 LLM 和交易所 API**——**没有公用 LLM**，不会自动借别人的 Key
- 平台只共享**基础因子**与**数据中心行情**；挖掘/训练因子按账户隔离  
详见 [`docs/账户隔离与自备配置说明.md`](docs/账户隔离与自备配置说明.md)、[`docs/数据中心与多账户并行架构说明.md`](docs/数据中心与多账户并行架构说明.md)。

浏览器预览（可选）：`cd frontend-next && npm run dev` → http://127.0.0.1:5273/login

打包安装包：`cd frontend-next && npm run electron:build` → 输出 `frontend-next/dist-electron/`

常用产物：
- **免安装可运行**：`frontend-next/dist-electron/win-unpacked/AlphaArena.exe`（`npm run electron:pack`）
- **安装包 NSIS**：需能访问 GitHub 下载 nsis 工具；失败时用免安装目录即可

若报 winCodeSign 符号链接错误：开 Windows「开发人员模式」，或已在配置中关闭 `signAndEditExecutable`。

---

## 浏览器方式：QUICK.bat / dev-start.bat（frontend-next :5273）

```
双击运行：QUICK.bat
或：dev-start.bat
```

正式前端是 **`frontend-next`（端口 5273）**。旧 `frontend` Vite `:5173` **已冻结**（见 `frontend/FROZEN.md`），不会再启动。

---

## 手动启动（开发）

**后端**：
```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

**正式前端**：
```powershell
cd frontend-next
npm run dev
```
→ http://127.0.0.1:5273/login

---

## 🛑 停止项目

```
双击运行：STOP.bat
```

---

## 📋 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| Python | ✅ 已安装 | 3.10.11 |
| Node.js | ✅ 已安装 | 22.14.0 |
| 前端依赖 | ✅ 已安装 | 412 packages |
| 后端依赖 | ❓ 待确认 | 需要运行 INSTALL-backend.bat |

---

## 📝 首次使用完整步骤

### 步骤1：安装后端依赖

```
双击运行：INSTALL-backend.bat
```

等待安装完成（可能需要5-10分钟），看到 "Installation Complete" 即可。

### 步骤2：启动项目

```
双击运行：QUICK.bat
```

### 步骤3：访问应用

浏览器会自动打开，或者手动访问：
- **前端**：http://127.0.0.1:5273/login
- **后端API**：http://localhost:8000/docs

---

## 🔄 日常使用

以后每次启动只需：
```
QUICK.bat
```

停止时：
```
STOP.bat
```

---

## ❓ 常见问题

### Q: 后端启动失败，提示模块未找到
**A**: 运行 `INSTALL-backend.bat` 安装后端依赖

### Q: 前端启动失败，提示 vite 命令未找到
**A**: 已解决 ✅ 前端依赖已安装

### Q: 端口被占用
**A**: 运行 `STOP.bat` 停止旧进程

### Q: 数据库连接失败
**A**: 确保PostgreSQL正在运行：
```bash
# 检查本地PostgreSQL服务（如已安装 PostgreSQL 15）
psql -U laobao -d alpha_arena -c "SELECT 1"
```

---

## 📂 项目文件说明

| 文件 | 说明 |
|------|------|
| QUICK.bat | ⭐ 每日启动使用 |
| STOP.bat | ⭐ 停止所有服务 |
| INSTALL-backend.bat | 首次安装后端依赖 |
| CHECK.bat | 检查环境配置 |

---

## 🎯 成功标志

启动成功后你会看到：

1. ✅ 两个新的命令行窗口打开
2. ✅ 后端窗口显示：`INFO: Application startup complete`
3. ✅ 前端窗口显示 Next.js 在 **http://127.0.0.1:5273**
4. ✅ 浏览器打开登录页（或使用 DESKTOP.bat 桌面窗）

---

## 💡 提示

- 首次使用需要先运行 `INSTALL-backend.bat`
- 后续直接用 `QUICK.bat` 启动即可
- 停止项目用 `STOP.bat`
- 如遇问题，查看两个窗口的错误信息

---

**现在可以运行 INSTALL-backend.bat 安装后端依赖了！**
