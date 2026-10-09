/**
 * 模拟盘「一屏一次请求」聚合回归（2026-09-18 前端刷新慢治理 §5.2）。
 *
 * ## 背景
 * 原为 4 个端点各 5s 轮询（真实访问日志里 `/paper/*` 占全站请求 50.7%），
 * 而后端单进程 GIL 长期贴 1 核 ⇒ 请求条数本身就是排队成本。
 * 现改为 `GET /api/paper/dashboard/{id}` 单请求 5s 轮询 + 四个派生 hook 只读。
 *
 * ## 三条断言，缺一条都会漏掉一类退化
 * 1. **不再打单端点**：若派生 hook 各自发请求 ⇒ 聚合白做（反而更重）。
 * 2. **请求数不放大**：TanStack v5 在已有数据时 `refetch()` 默认 `cancelRefetch`
 *    ⇒ 多个观察者共享 key 且各自带 `refetchInterval` 时，每间隔会发 N 次。
 *    故钉住"20s 窗口内聚合请求数 ≈ 4"（下界 3、上界 8）。
 * 3. **只挂派生的页面仍在轮询**：`/dashboard` 只调用 `usePaperBalance`/`usePositions`
 *    （皆派生），若轮询只挂在某个显式"轮询器 hook"上，这类页面会**静默失去刷新**。
 *
 * 会话：注入本地假 token（后端对回环请求走本地租户通道），不需要 .env.e2e 账号。
 * 前置：后端 :8000 与前端 dev :5273 已运行，且后端已含 `/api/paper/dashboard`。
 */
import { test, expect, type Page } from "@playwright/test";

const NS_KEY = "arena_last_paper_account_id";
const AGG = "/api/paper/dashboard/";
const SINGLES = [
  "/api/paper/balance/",
  "/api/paper/positions/",
  "/api/paper/orders/",
  "/api/paper/summary/",
];

/** 必须在 goto 之前挂监听，否则会漏掉首屏请求。 */
function counter(page: Page) {
  const hits: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (!u.includes(":8000/api/")) return;
    hits.push(new URL(u).pathname);
  });
  return {
    agg: () => hits.filter((p) => p.startsWith(AGG)).length,
    singles: () => hits.filter((p) => SINGLES.some((s) => p.startsWith(s))),
  };
}

test.describe("模拟盘聚合端点：一屏一次请求", () => {
  test.describe.configure({ timeout: 150_000 });

  test.beforeEach(async ({ context }) => {
    await context.addInitScript((key) => {
      try {
        localStorage.setItem("arena_access_token", "e2e-dummy");
        localStorage.setItem("arena_refresh_token", "e2e-dummy");
        localStorage.setItem("arena_has_session", "1");
        localStorage.setItem(key, "14");
      } catch {
        /* ignore */
      }
    }, NS_KEY);
  });

  test("模拟盘页：聚合在轮询、单端点 0 次、请求数不放大", async ({ page }) => {
    const c = counter(page);
    await page.goto("/paper-trading", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForTimeout(21_000); // ≈4 个轮询周期

    const agg = c.agg();
    const singles = c.singles();
    expect(agg, `聚合请求应 ≥3 次（轮询在跑），实际 ${agg}`).toBeGreaterThanOrEqual(3);
    expect(
      singles.length,
      `派生 hook 仍在打单端点：${[...new Set(singles)].slice(0, 6).join(", ")}`,
    ).toBe(0);
    // 20s / 5s ≈ 4；多观察者各自轮询会变成 ~16。上界 8 足以捕捉乘法又容忍抖动。
    expect(agg, `聚合请求 ${agg} 次疑似放大（多观察者各自轮询）`).toBeLessThanOrEqual(8);
  });

  test("仪表盘页（只挂派生 hook）仍在轮询、单端点 0 次", async ({ page }) => {
    const c = counter(page);
    await page.goto("/dashboard", { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForTimeout(16_000);

    const agg = c.agg();
    const singles = c.singles();
    expect(
      agg,
      `只挂派生 hook 的页面也必须轮询（否则静默失去刷新），实际聚合 ${agg} 次`,
    ).toBeGreaterThanOrEqual(2);
    expect(
      singles.length,
      `仪表盘页不应再打单端点：${[...new Set(singles)].slice(0, 6).join(", ")}`,
    ).toBe(0);
  });
});
