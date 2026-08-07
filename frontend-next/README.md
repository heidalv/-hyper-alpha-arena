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
npm run electron:build   # Next 导出 + 安装包（NSIS 需能访问 GitHub）
# 或指定生产 API：
npm run electron:build:prod
```

生产模式由 Electron 在本机起静态 HTTP 提供 `out/`，默认 API `http://localhost:8000`（登录页可改）。
产物目录：`dist-electron/`（见 `electron-builder.yml`）。
