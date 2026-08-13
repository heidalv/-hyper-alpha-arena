import { defineConfig } from "@playwright/test";

/**
 * Playwright 最小 E2E（R7-2）
 * 前置：后端 :8000 与 frontend-next dev :5273 已在运行（本配置不自动起服务）。
 * 运行：cd frontend-next && npx playwright test
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5273",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
