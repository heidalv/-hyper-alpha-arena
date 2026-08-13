# Heidalv Alpha Arena — 新前端 (frontend-next)

独立桌面终端前端（Next.js + Electron），与后端 FastAPI 分离部署。

## 开发

```bash
# 终端1：后端 :8000
# 终端2：前端
cd frontend-next
npm run dev          # http://127.0.0.1:5273 浏览器预览

# 桌面壳（不依赖系统浏览器窗口，自带 Electron）
npm run electron:dev
```

首次使用在登录页 **注册** 账号，或登录已有用户。  
Token：浏览器开发用 localStorage；Electron 用系统 **safeStorage** 加密落盘。

可选环境变量见 `.env.example`：`NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL`。

## 登录鉴权

- 登录页：`/login`（用户名或邮箱 + 密码；可注册）
- API：`Authorization: Bearer <access>`；401 自动 `/api/auth/refresh`
- 顶栏显示用户名与退出

## VIP AI 选币

- 路由：`/coin-select`（侧栏仅 VIP / 管理员显示）
- 说明：仓库根目录 [`docs/VIP共用AI选币说明.md`](../docs/VIP共用AI选币说明.md)

## 打包桌面程序

```bash
npm run electron:pack    # 免安装目录 dist-electron/win-unpacked/AlphaArena.exe
npm run electron:build   # Next 导出 + 安装包（NSIS）
```

生产模式由 Electron 在本机起静态 HTTP 提供 `out/`，默认 API `http://localhost:8000`（登录页可改）。
产物目录：`dist-electron/`（见 `electron-builder.yml`）。

## 推送更新（给已安装的 EXE 用）

三步：

1. **打包并推送**（在仓库根目录）  
   `npm run desktop:publish`  
   或：`powershell -File scripts/publish-desktop.ps1`  
   会把安装包复制到 `releases/desktop/`（后端通过 `/arena-updates/` 对外提供）。

2. **保证后端已启动**，并监听所有网卡：`BACKEND_HOST=0.0.0.0`（Tailscale 远程才能下到包）。

3. **已安装的 EXE**：登录页填同一后端地址（如 `http://100.100.175.17:8000`）→ 点「检查更新」或等自动检查 → 下载完点「立即安装并重启」。

验证：浏览器打开 `http://127.0.0.1:8000/arena-updates/latest.yml` 应能看到版本号。
