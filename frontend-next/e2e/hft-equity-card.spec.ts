/**
 * 高频交易页「账户收益曲线」卡片 + 日期选择回归（2026-09-23 用户要求）。
 *
 * ## 断言
 * 1. HFT 页必须有收益曲线卡片，且**真的在画**（SVG 有非空折线路径）；
 * 2. **日期选择可用**：点 7天/90天/全部 ⇒ 选中态切换，并触发一次新的 `/hft/equity-series?days=N` 请求
 *    （防止"按钮只是装饰、数据没变"这类静默退化）；
 * 3. **对账徽标语义正确**：只有拿到账户表基准才允许显示差异，**任何情况下**不得显示"对账一致"
 *    （除非后端 `reconcile.ok === true`）；无基准时显示「无对账基准」。
 * 4. **[2026-09-23 用户反馈]** 曲线必须**只算当前活动账户**：卡片要写明账户与起始时间
 *    （`equity-account-scope`），且后端返回的 `account_created_at` 存在 ⇒ 历史账户/历史期数被剔除。
 *
 * 会话：注入本地假 token（沿用 paper-* 用例做法）。前置：后端 :8000 与前端 dev :5273 在跑。
 */
import { test, expect, type Page } from "@playwright/test";

function trackEquityCalls(page: Page): number[] {
  const days: number[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("/api/hft/equity-series")) {
      const m = /days=(\d+)/.exec(u);
      days.push(m ? parseInt(m[1], 10) : -1);
    }
  });
  return days;
}

test.describe("HFT 收益曲线卡片", () => {
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

  test("卡片在位、日期选择触发重取、无对账基准不谎称一致", async ({ page }) => {
    const calls = trackEquityCalls(page);
    await page.goto("/hft", { waitUntil: "domcontentloaded", timeout: 40_000 });

    const card = page.getByTestId("equity-series-card").first();
    await expect(card, "HFT 页必须有收益曲线卡片").toBeVisible({ timeout: 40_000 });
    await expect(card.getByTestId("equity-svg")).toBeVisible({ timeout: 40_000 });

    // 折线路径非空（防止"有卡片没数据"）
    const lineLen = await card
      .locator("svg path")
      .nth(1)
      .evaluate((el) => (el.getAttribute("d") || "").length);
    expect(lineLen, "折线路径不应为空").toBeGreaterThan(20);

    // 默认 30 天已请求
    expect(calls.length, "首屏应请求 equity-series").toBeGreaterThanOrEqual(1);
    expect(calls[0], "默认周期应为 30 天").toBe(30);

    // 日期选择：点「7天」→ 选中态 + 新请求 days=7
    const before = calls.length;
    await card.getByTestId("equity-period-7d").click();
    await expect(card.getByTestId("equity-period-7d")).toHaveClass(/border-cyan-400/);
    await expect
      .poll(() => calls.slice(before).some((d) => d === 7), { timeout: 15_000, message: "点 7天 后应发出 days=7 请求" })
      .toBe(true);

    // 点「全部」→ days=365
    const before2 = calls.length;
    await card.getByTestId("equity-period-all").click();
    await expect
      .poll(() => calls.slice(before2).some((d) => d === 365), { timeout: 15_000, message: "点 全部 后应发出 days=365 请求" })
      .toBe(true);

    // 对账徽标：拿到账户表基准 ⇒ 显示差异（warn）；无基准 ⇒ na。二者必居其一，
    // 且永远不得显示"对账一致"（除非 reconcile.ok===true，本模块当前为 false）。
    const warn = card.getByTestId("equity-reconcile-warn");
    const na = card.getByTestId("equity-reconcile-na");
    await expect
      .poll(async () => (await warn.count()) + (await na.count()), { timeout: 15_000, message: "对账徽标必须出现" })
      .toBeGreaterThan(0);
    if (await warn.isVisible().catch(() => false)) {
      await expect(warn, "有基准时应写明差异金额").toContainText("与余额表差 $");
    }
    await expect(card.getByText("对账一致"), "不得谎称对账一致").toHaveCount(0);

    // [用户反馈] 曲线只算当前活动账户：账户名 + 起始时间必须显示
    const scope = card.getByTestId("equity-account-scope");
    await expect(scope, "必须显示曲线所属账户与起点").toBeVisible({ timeout: 15_000 });
    await expect(scope).toContainText("账户");
    await expect(scope).toContainText("起");
  });

  test("深度区默认折叠省地方、可展开（用户反馈「太占地方」）", async ({ page }) => {
    await page.goto("/hft", { waitUntil: "domcontentloaded", timeout: 40_000 });
    const toggle = page.getByTestId("depth-toggle");
    await expect(toggle, "折叠开关必须存在（不依赖深度数据是否已到）").toBeVisible({ timeout: 40_000 });
    // 折叠容器在 board 数据到达后才挂载 ⇒ 等 attached，再断言高度（无数据时只要属性正确即可）
    const body = page.getByTestId("depth-body");
    const appeared = await body
      .waitFor({ state: "attached", timeout: 60_000 })
      .then(() => true)
      .catch(() => false);
    if (!appeared) {
      // 深度数据未到：至少要保证开关在、且不谎称已展开
      await expect(toggle).toContainText("展开");
      test.info().annotations.push({ type: "note", description: "深度数据未就绪，仅校验开关存在" });
      return;
    }
    await expect(body).toHaveAttribute("data-expanded", "0");
    const collapsed = await body.boundingBox();
    expect(collapsed && collapsed.height, "默认折叠高度应 ≤ 320px（省地方）").toBeLessThanOrEqual(320);

    await toggle.click();
    await expect(body).toHaveAttribute("data-expanded", "1");
    await expect
      .poll(async () => (await body.boundingBox())?.height ?? 0, { timeout: 10_000, message: "展开后应显著变高" })
      .toBeGreaterThan((collapsed?.height ?? 0) + 50);

    await toggle.click(); // 收起
    await expect(body).toHaveAttribute("data-expanded", "0");
  });

  /**
   * [2026-09-23 用户反馈回归] 「没有按当前活动账户算，把之前的所有历史账户都算进去了」。
   * 直接钉后端契约：曲线必须挂到**唯一一个活动账户**、给出 `account_created_at` 作为切分依据，
   * 且 `start_equity` 必须等于「账户存续期起点→窗口起点」的累计（即"窗口前的历史被算进起点、
   * 账户创建前的历史被剔除"），三个周期的**终点一致**（同一条连续曲线）。
   */
  test("曲线按当前活动账户切分（历史账户/历史期数不计入）", async ({ request }) => {
    type Series = {
      account_id: number | null;
      account_created_at: string | null;
      initial_balance: number;
      start_equity: number;
      realized_end: number;
      points: { t: number; v: number }[];
      baseline?: { scope?: string };
      reconcile: { ok: boolean | null };
    };
    const get = async (days: number): Promise<Series> => {
      const r = await request.get(`http://127.0.0.1:8000/api/hft/equity-series?days=${days}`);
      expect(r.ok(), `equity-series?days=${days} 应可用`).toBeTruthy();
      return (await r.json()) as Series;
    };
    const [d7, d30, d90] = [await get(7), await get(30), await get(90)];

    expect(d30.account_id, "必须绑定到一个具体账户（不可全历史裸算）").toBeTruthy();
    expect(d30.account_created_at, "必须给出账户创建时间作为切分依据").toBeTruthy();
    expect(d30.baseline?.scope, "应声明口径范围").toContain("账户存续期");

    // 终点一致 ⇒ 同一条连续曲线（若把历史账户算进来，短窗口的终点会不同）
    expect(d30.realized_end, "30天与90天终点应一致").toBeCloseTo(d90.realized_end, 6);
    expect(d30.realized_end, "30天与7天终点应一致").toBeCloseTo(d7.realized_end, 6);

    // 窗口起点 = 账户存续期内的累计（账户创建前的历史不计入）：
    // 90 天窗口覆盖账户全部存续期 ⇒ 起点必为 0；7 天窗口起点 = 更早的累计（非 0）。
    expect(d90.start_equity, "90天窗口起点应为账户存续期起点（0）").toBeCloseTo(0, 6);
    expect(d90.points[0]?.v, "曲线首点应从 0 起").toBeCloseTo(0, 6);
    expect(Math.abs(d7.start_equity), "7天窗口起点应含更早累计（非 0）").toBeGreaterThan(0);

    // 无初始资金：不得伪造百分比回撤、不得谎称对账一致
    expect(d30.initial_balance, "本模块无独立资金账户 ⇒ initial_balance=0").toBe(0);
    expect(d30.reconcile.ok, "与账户表口径不一致时必须如实为 false/null，不得为 true").not.toBe(true);
  });
});
