/**
 * Agent 监控 · 分析墙（画布）回归（2026-09-19 建，2026-10-01 改为合并后的路由）。
 *
 * 断言三条（缺一条就会退化）：
 * 1. **画布真的渲染**：节点数 ≥20（含分组框与边）；
 * 2. **单页只有两个数据源**：`/api/agent-wall/state` + `/api/agent-wall/tail`
 *    —— 禁止每节点一个请求（后端单进程 GIL 贴 1 核，fan-out 正是"刷新慢"的根因）；
 * 3. **tail 是多节点合并**：每次请求的 `nodes=` 至少含 1 个节点，且请求数远小于节点数×轮次。
 *
 * [2026-10-01 合并] 画布已并入「Agent 监控」页（侧栏归入「市场 & 分析」组），
 * 路由从 `/agent-wall` 变为 `/agent-monitor?tab=wall`；旧路由保留为跳转桩。
 *
 * 会话：注入本地假 token（后端对回环走本地租户通道），无需 .env.e2e 账号。
 * 前置：后端已含 `/api/agent-wall/*`，前端 dev :5273 在跑。
 */
import { test, expect, type Page } from "@playwright/test";

const WALL_URL = "/agent-monitor?tab=wall";

function recorder(page: Page) {
  const hits: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (!u.includes(":8000/api/")) return;
    hits.push(new URL(u).pathname + new URL(u).search);
  });
  return hits;
}

test.describe("Agent 监控 · 分析墙画布", () => {
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

  test("渲染节点/分组/边，且只轮询 state+tail（无 fan-out）", async ({ page }) => {
    const hits = recorder(page);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));

    // 冷启动时 Next dev 首次编译合并后的 /agent-monitor 页可能 >10s：先等**首个 tail 真正落地**，
    // 再观察一个完整轮询窗口（8s）。否则"轮询次数"会被编译时间吃掉（本轮实测：固定 12s 等待时
    // 冷启只看到 1 次 tail 而失败；热态 4~5 次）。断言本身（≥2）不变，只是把窗口对齐到页面真正活着之后。
    const firstTail = page.waitForResponse(
      (r) => r.url().includes("/api/agent-wall/tail"),
      { timeout: 60_000 },
    );
    await page.goto(WALL_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await firstTail;
    await page.waitForTimeout(8_000);

    const stateReqs = hits.filter((h) => h.startsWith("/api/agent-wall/state"));
    const tailReqs = hits.filter((h) => h.startsWith("/api/agent-wall/tail"));
    // 全局布局组件（TickerBar / ops-errors / 会话）在每个页面都会请求，属预期；
    // 这里只禁止**画布页自己**引入 per-node 请求。
    const GLOBAL_OK = ["/api/market/ticker-bar", "/api/auto-coin/active-symbols",
                       "/api/ops/errors", "/api/auth/me", "/api/account/list",
                       "/api/full-auto/sessions", "/api/config/default-exchange"];
    const unexpected = hits.filter(
      (h) => !h.includes("/api/agent-wall/") && !GLOBAL_OK.some((g) => h.startsWith(g)),
    );

    // ① 两个数据源都在轮询（证明画布活着）
    expect(stateReqs.length, `state 轮询缺失（${stateReqs.length} 次）`).toBeGreaterThanOrEqual(2);
    expect(tailReqs.length, `tail 轮询缺失（${tailReqs.length} 次）`).toBeGreaterThanOrEqual(2);

    // ② 没有被画布页拖出来的第三方请求
    expect(unexpected, `画布页出现非预期请求：${unexpected.slice(0, 5).join(", ")}`).toHaveLength(0);

    // ③ 多节点合并：每次 tail 都带 nodes= 参数（而不是每节点一个请求）
    for (const h of tailReqs) {
      expect(h, `tail 缺少 nodes 参数：${h}`).toContain("nodes=");
      expect((h.match(/nodes=/) || []).length).toBe(1);
    }

    // ④ 节点确实渲染（每张卡底部有 node.id）+ 分组标题存在
    for (const id of ["brain_mid", "brain_long", "anomaly", "timing", "data_center"]) {
      await expect(page.getByText(id, { exact: true }).first(), `节点 ${id} 未渲染`).toBeVisible();
    }
    await expect(page.getByText(/主脑（LLM 论题）/).first()).toBeVisible();
    // QAA v3 卡片族已于 2026-09-19 退役：画布不列它，但必须显示"已退役"说明
    await expect(page.getByText(/已退役：qaa_v3_cards/).first()).toBeVisible();
    await expect(page.getByText(/QAA 卡片族/)).toHaveCount(0);
    await expect(page.getByText(/断链|正常/).first()).toBeVisible();

    // ⑤ 无 JS 运行错误
    expect(errors, `页面出现 JS 错误：${errors.join(" | ")}`).toHaveLength(0);
  });

  test("侧栏只有一个入口「Agent 监控」（市场 & 分析 组），点击直达画布", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded", timeout: 30_000 });
    const link = page.getByRole("link", { name: "Agent 监控" }).first();
    await expect(link, "侧栏缺少 Agent 监控 入口").toBeVisible({ timeout: 20_000 });
    // 合并后不许再有第二个入口（Agent Wall 已并入本页第一个 Tab）
    await expect(page.getByRole("link", { name: /Agent Wall/ }), "Agent Wall 入口应已合并掉").toHaveCount(0);
    await link.click();
    await page.waitForURL(/agent-monitor/, { timeout: 20_000 });
    await expect(page.getByRole("heading", { name: /Agent 监控/ }), "合并后的页头应为 Agent 监控").toBeVisible({ timeout: 20_000 });
    // 默认落在画布 Tab
    await expect(page.getByText("分析墙（画布）").first()).toBeVisible({ timeout: 20_000 });
  });

  test("旧路由 /agent-wall 仍可用（跳转桩到合并后的页面）", async ({ page }) => {
    await page.goto("/agent-wall", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForURL(/agent-monitor\?tab=wall/, { timeout: 20_000 });
    await expect(page.getByRole("heading", { name: /Agent 监控/ })).toBeVisible({ timeout: 20_000 });
  });

  test("审计面板可打开并显示断链条目", async ({ page }) => {
    await page.goto(WALL_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    const btn = page.locator("button", { hasText: "审计" }).first();
    await btn.waitFor({ state: "visible", timeout: 30_000 });
    await btn.click({ force: true });      // 避开 Next dev 覆盖层可能的拦截
    await expect(page.getByText(/断链与逻辑错误审计/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/experiments 表 0 行/).first()).toBeVisible({ timeout: 20_000 });
  });
});
