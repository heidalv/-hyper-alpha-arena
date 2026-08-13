/**
 * 导航冒烟：登录 → 仪表盘 → 各核心页面不报错（R7-2）
 * 只读断言，绝不触发下单。账号来自 .env.e2e（不提交真实密码）：
 *   E2E_USER / E2E_PASS
 */
import { test, expect } from "@playwright/test";

test.describe("核心导航冒烟", () => {
  test.beforeEach(async ({ page }) => {
    const user = process.env.E2E_USER;
    const pass = process.env.E2E_PASS;
    test.skip(!user || !pass, "未配置 E2E_USER/E2E_PASS（.env.e2e），跳过");

    await page.goto("/login");
    await page.getByPlaceholder(/username|用户名|邮箱/i).first().fill(user!);
    await page.getByPlaceholder(/password|密码/i).first().fill(pass!);
    await page.getByRole("button", { name: /登录|登 录|login/i }).click();
    await page.waitForURL(/dashboard/, { timeout: 30_000 });
  });

  test("仪表盘渲染核心区块", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /仪表盘/i })).toBeVisible();
    await expect(page.getByText("今日 P&L 归因")).toBeVisible();
    await expect(page.getByText(/权益曲线/i)).toBeVisible();
  });

  test("侧栏导航到模拟交易与运维看板", async ({ page }) => {
    await page.getByRole("link", { name: /模拟交易/i }).click();
    await page.waitForURL(/paper-trading/, { timeout: 20_000 });
    await expect(page.getByRole("heading", { name: /模拟交易/i })).toBeVisible();

    await page.getByRole("link", { name: /运维看板/i }).click();
    await page.waitForURL(/ops/, { timeout: 20_000 });
    await expect(page.getByText(/健康总览|心跳正常/i).first()).toBeVisible();
  });

  test("顶栏搜索框唤起命令面板", async ({ page }) => {
    await page.getByPlaceholder(/搜索.*Ctrl\+K/i).click();
    await expect(page.getByPlaceholder("搜索页面或功能...")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByPlaceholder("搜索页面或功能...")).not.toBeVisible();
  });
});
