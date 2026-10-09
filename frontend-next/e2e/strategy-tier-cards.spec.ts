/**
 * 「中线配置 / 长线配置」并入「AI 策略」页内卡片（2026-10-01 用户指令）回归。
 *
 * 用户原话：「中线配置 长线配置 并入 ai策略 作为页内的卡片」。
 * 断言四条（缺一条就会退化）：
 * 1. **侧栏导航已收敛**：策略配置组只剩 AI 策略 / VIP AI 选币，不再有中/长线独立入口；
 * 2. **页内卡片真的在**：/strategy 有 #tier-config-mid、#tier-config-long 两张卡，且卡内确有参数控件；
 * 3. **折叠可用**：收起后显示摘要（「N 组参数 …」），不是直接消失；
 * 4. **旧路由不失效**：/mid、/long（含 ?tab=prompts）跳转桩仍落到 /strategy?cfg=…[&sub=…]。
 *
 * 会话：注入本地假 token（后端对回环走本地租户通道），无需 .env.e2e 账号。
 * 前置：前端 dev :5273 在跑（后端 :8000 供参数接口；接口挂了卡片会显示「加载失败 + 重试」，
 * 此时第 2 条会失败——这正是我们要的判别力，不算 flaky）。
 */
import { test, expect } from "@playwright/test";

test.describe("AI 策略 · 车道配置页内卡片", () => {
  test.describe.configure({ timeout: 120_000 });

  test.beforeEach(async ({ context }) => {
    await context.addInitScript(() => {
      try {
        localStorage.setItem("arena_access_token", "e2e-dummy");
        localStorage.setItem("arena_refresh_token", "e2e-dummy");
        localStorage.setItem("arena_has_session", "1");
      } catch {
        /* ignore */
      }
    });
  });

  test("页内两张配置卡 + 折叠摘要 + 侧栏不再单列入口", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));

    await page.goto("/strategy", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForTimeout(9_000);

    // ① 侧栏：策略配置组只剩两项
    const nav = await page.locator("aside nav").innerText();
    expect(nav).toContain("策略配置");
    expect(nav).toContain("AI 策略");
    expect(page.getByRole("link", { name: "中线配置" }), "侧栏不应再有中线配置入口").toHaveCount(0);
    expect(page.getByRole("link", { name: "长线配置" }), "侧栏不应再有长线配置入口").toHaveCount(0);

    // ② 两张页内卡片 + 卡内参数控件
    const mid = page.locator("#tier-config-mid");
    const long = page.locator("#tier-config-long");
    await expect(mid, "缺少中线配置页内卡").toBeVisible({ timeout: 20_000 });
    await expect(long, "缺少长线配置页内卡").toBeVisible({ timeout: 20_000 });
    expect(await mid.locator("input[type=range]").count(), "中线卡应有参数滑块").toBeGreaterThan(0);
    expect(await long.locator("input[type=range]").count(), "长线卡应有参数滑块").toBeGreaterThan(0);
    // 长线卡独有「报告观测」子页签
    await expect(long.getByText("报告观测")).toBeVisible();

    // ③ 折叠后显示摘要（而不是整卡消失）
    await mid.locator("button", { hasText: "收起" }).first().click();
    await expect(mid).toBeVisible();
    await expect(mid.getByText(/组参数/)).toBeVisible({ timeout: 10_000 });
    await expect(mid.locator("button", { hasText: "展开配置" }).first()).toBeVisible();

    // ⑤ 无 JS 运行错误
    expect(errors, `页面出现 JS 错误：${errors.join(" | ")}`).toHaveLength(0);
  });

  test("旧路由 /mid、/long 跳转桩带 cfg/sub 落到 AI 策略", async ({ page }) => {
    await page.goto("/mid", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForURL(/strategy\?cfg=mid/, { timeout: 20_000 });

    await page.goto("/long", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForURL(/strategy\?cfg=long/, { timeout: 20_000 });

    // 旧页的 ?tab=prompts 映射为卡内 &sub=prompts
    await page.goto("/long?tab=prompts", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForURL(/strategy\?cfg=long&sub=prompts/, { timeout: 20_000 });
    await expect(page.locator("#tier-config-long")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("#tier-config-long").getByText("提示词")).toBeVisible();
  });
});
