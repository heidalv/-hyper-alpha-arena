# 旧 Vite 前端已冻结（:5173）

**正式前端是 `frontend-next`（端口 5273）**，不是本目录。

- 日常启动：根目录 `dev-start.bat` / `DESKTOP.bat`，或 `cd frontend-next && npm run dev`
- 本目录的 `pnpm dev` / `vite` **已禁用**，防止误起 5173
- 需要考古时再用 `pnpm run dev:unfrozen`（仅本地调试，勿作默认）

说明见仓库根 `README.md` 与 `frontend-next/README.md`。
