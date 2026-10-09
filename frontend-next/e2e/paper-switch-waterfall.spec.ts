/**
 * 切到模拟盘页的「波1/波2 并行」回归测试（2026-09-18 前端刷新慢治理 / F3b）。
 *
 * ## 背景（实测）
 * `frontend-next/src/app/paper-trading/page.tsx` 原写：
 *   `const activeAccountId = selectedAccountId ?? paperAccounts[0]?.id ?? null`
 * 而 `paperAccounts` 来自 `useAccounts()`（`/api/account/list`）；四个数据 hook 都是
 * `enabled: !!accountId` ⇒ 切页必须**先等账户列表返回**才发数据请求（两级瀑布）。
 * 实测账户列表慢到 1035ms 时，四个数据请求就要白等这 1035ms
 * （`tools/measure-switch-wave.mjs` 的 B/C 对照）。
 *
 * 修复：账户列表未返回时用"上次用过的账户 id"（localStorage）乐观兜底，
 * 让波2 与波1 并行；列表一到即回到原判定。
 *
 * ## 本测试
 * 1. 机制在位：首次进入会把当前账户写入 localStorage；
 * 2. 有缓存 ⇒ 四个数据请求**不等**账户列表（间隔远小于账户耗时）；
 * 3. 对照：清掉缓存后该量**能**测出等待（防止断言变成空断言）。
 *
 * 前置：后端 :8000 与前端 dev :5273 已运行（同 playwright.config.ts 说明）。
 * 会话：注入本地假 token —— 后端对回环请求走本地租户通道，`/auth/me` 正常返回，
 * 因此**不需要** .env.e2e 账号密码，也不写数据库。
 */
import { test, expect, type Page } from "@playwright/test";

const NS_KEY = "arena_last_paper_account_id";
const PAPER_PREFIXES = [
  "/api/paper/balance/",
  "/api/paper/positions/",
  "/api/paper/orders/",
  "/api/paper/summary/",
];

interface Sample {
  accountMs: number | null;
  gapToWave2: number | null;
  readyMs: number | null;
  paperCount: number;
}

function attach(page: Page) {
  const events: { kind: "req" | "res"; t: number; path: string }[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (!u.includes(":8000/api/")) return;
    events.push({ kind: "req", t: Date.now(), path: new URL(u).pathname });
  });
  page.on("response", (r) => {
    const u = r.url();
    if (!u.includes(":8000/api/")) return;
    events.push({ kind: "res", t: Date.now(), path: new URL(u).pathname });
  });
  return events;
}

/** 打开模拟盘页并等到四个数据端点都有响应（或 25s 超时）。 */
async function visitAndWait(page: Page, events: ReturnType<typeof attach>) {
  const t0 = Date.now();
  await page.goto("/paper-trading", { waitUntil: "domcontentloaded", timeout: 30_000 });
  const deadline = Date.now() + 25_000;
  while (Date.now() < deadline) {
    const done = new Set(events.filter((e) => e.kind === "res").map((e) => e.path));
    if (PAPER_PREFIXES.every((p) => [...done].some((d) => d.startsWith(p)))) break;
    await page.waitForTimeout(50);
  }
  return t0;
}

function analyze(events: ReturnType<typeof attach>, t0: number): Sample {
  const rel = (t: number) => t - t0;
  const acctReq = events.find((e) => e.kind === "req" && e.path === "/api/account/list");
  const acctRes = events.find((e) => e.kind === "res" && e.path === "/api/account/list");
  const paperReq = events.filter((e) => e.kind === "req" && e.path.startsWith("/api/paper/"));
  const paperRes = events.filter((e) => e.kind === "res" && e.path.startsWith("/api/paper/"));
  if (!acctReq || paperReq.length === 0) {
    return { accountMs: null, gapToWave2: null, readyMs: null, paperCount: paperReq.length };
  }
  return {
    accountMs: acctRes ? rel(acctRes.t) - rel(acctReq.t) : null,
    gapToWave2: Math.min(...paperReq.map((r) => rel(r.t))) - rel(acctReq.t),
    readyMs: paperRes.length ? Math.max(...paperRes.map((r) => rel(r.t))) : null,
    paperCount: paperReq.length,
  };
}

test.describe("模拟盘切页：数据请求不等待账户列表（波1/波2 并行）", () => {
  test.describe.configure({ timeout: 120_000 });

  test.beforeEach(async ({ context }) => {
    // 本地会话（见文件头说明）：无法解析的 token ⇒ 不触发续期；/auth/me 走本地租户通道。
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

  test("首次进入会把当前模拟盘账户写入 localStorage", async ({ page }) => {
    const events = attach(page);
    await visitAndWait(page, events);
    await expect
      .poll(async () => page.evaluate((k) => localStorage.getItem(k), NS_KEY), { timeout: 10_000 })
      .not.toBeNull();
    const raw = await page.evaluate((k) => localStorage.getItem(k), NS_KEY);
    expect(Number(raw)).toBeGreaterThan(0);
  });

  test("有缓存 ⇒ 四个数据请求不等账户列表", async ({ page, context }) => {
    // 预热：让页面写入"上次用过的账户"
    const warm = attach(page);
    await visitAndWait(page, warm);
    const cached = await page.evaluate((k) => localStorage.getItem(k), NS_KEY);
    expect(Number(cached)).toBeGreaterThan(0);

    // 另开一页（同一 context ⇒ 共享 localStorage），测量一次冷切页
    const fresh = await context.newPage();
    const events = attach(fresh);
    const t0 = await visitAndWait(fresh, events);
    const s = analyze(events, t0);

    expect(s.paperCount, "应有四个纸面数据请求").toBeGreaterThanOrEqual(4);
    expect(s.gapToWave2, "未采到波2 首个请求").not.toBeNull();
    // 判据：不等待账户列表。留出调度余量：间隔不得超过账户耗时的 40% 或 120ms（取较大者）。
    const budget = Math.max(120, (s.accountMs ?? 0) * 0.4);
    expect(
      s.gapToWave2!,
      `波2 首个请求距账户列表开始 ${Math.round(s.gapToWave2!)}ms，`
        + `账户列表耗时 ${Math.round(s.accountMs ?? -1)}ms —— 说明又在串行等待`,
    ).toBeLessThanOrEqual(budget);
    await fresh.close();
  });

  test("对照：清掉缓存后该量能测出等待（断言非空转）", async ({ page, context }) => {
    const warm = attach(page);
    await visitAndWait(page, warm);

    const noCache = await context.newPage();
    await noCache.goto("/factors", { waitUntil: "domcontentloaded" });
    await noCache.evaluate((k) => localStorage.removeItem(k), NS_KEY);
    const events = attach(noCache);
    const t0 = await visitAndWait(noCache, events);
    const s = analyze(events, t0);
    await noCache.close();

    test.skip(
      s.accountMs == null || s.accountMs < 150,
      `本机账户列表仅 ${Math.round(s.accountMs ?? 0)}ms，太短无法体现串行等待；`
        + "该对照在慢响应时才具判别力",
    );
    // 无缓存时波2 必须等账户列表：间隔应接近账户耗时（≥50%）
    expect(
      s.gapToWave2!,
      `无缓存时波2 间隔 ${Math.round(s.gapToWave2!)}ms，账户耗时 ${Math.round(s.accountMs!)}ms`,
    ).toBeGreaterThanOrEqual(s.accountMs! * 0.5);
  });
});
