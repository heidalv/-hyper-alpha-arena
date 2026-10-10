/**
 * 学习进化新页签的冒烟（[2026-10-10 前端升级]）
 *
 * 覆盖：
 *   1. 深链 `?tab=inlet` / `?tab=advisor` 直达（侧栏子项走的就是这两个 URL）；
 *   2. 已在本页时**二次点击**侧栏子项要真的切页签（`useState` 初值只在挂载求值，
 *      不修的话 URL 变了内容不变 —— 用户会以为链接坏了）；
 *   3. 新接口全部 500 时，面板要显示分块错误而不是白屏（断链如实展示）。
 *
 * 账号来自 .env.e2e（E2E_USER / E2E_PASS），未配置则整体跳过。
 */
import { test, expect } from "@playwright/test";

test.describe("学习进化 · 车道页签", () => {
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

  test("深链直达「学习入口」与「车道顾问」", async ({ page }) => {
    await page.goto("/intelligent-learning?tab=inlet");
    await expect(page.getByRole("heading", { name: /智能学习中心/i })).toBeVisible();
    // 入口面板的标题（不依赖后端是否可达：标题是静态的）
    await expect(page.getByText(/高频入口进度/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: /手动补扫一次/ })).toBeVisible();

    await page.goto("/intelligent-learning?tab=advisor");
    await expect(page.getByText(/旋钮白名单/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("advisor-lane-hft")).toBeVisible();
    await expect(page.getByText(/决策时间轴/).first()).toBeVisible();
  });

  test("同页二次导航：点侧栏子项要真的切页签", async ({ page }) => {
    await page.goto("/intelligent-learning");
    // 先落在默认页签
    await expect(page.getByText(/链路体检/).first()).toBeVisible({ timeout: 20_000 });

    // 点侧栏「车道顾问」子项（同 path + 不同 query）
    await page.getByRole("link", { name: /车道顾问/i }).first().click();
    await expect(page).toHaveURL(/tab=advisor/);
    await expect(page.getByText(/旋钮白名单/).first()).toBeVisible({ timeout: 20_000 });

    // 再点「学习入口」：仍要切换（这条是 useState 初值陷阱的回归断言）
    await page.getByRole("link", { name: /学习入口/i }).first().click();
    await expect(page).toHaveURL(/tab=inlet/);
    await expect(page.getByText(/高频入口进度/).first()).toBeVisible({ timeout: 20_000 });
  });

  test("新接口全 500 时显示错误而不是白屏", async ({ page }) => {
    await page.route("**/api/learning/lane/**", (route) =>
      route.fulfill({ status: 500, body: JSON.stringify({ detail: "boom" }) }));

    await page.goto("/intelligent-learning?tab=inlet");
    // 面板本体仍在（静态结构），并出现错误提示
    await expect(page.getByText(/高频入口进度/).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/请求失败|失败|boom/i).first()).toBeVisible({ timeout: 20_000 });

    await page.goto("/intelligent-learning?tab=advisor");
    await expect(page.getByText(/旋钮白名单/).first()).toBeVisible({ timeout: 20_000 });
  });
});
