/**
 * 模拟盘页「挂单情况」面板回归（2026-09-23 用户需求）。
 *
 * ## 背景与断言
 * 用户判定原「手动下单」模块无用 → 已从 `app/paper-trading/page.tsx` 移除，
 * 同一位置换成「挂单情况」= 交易所侧**挂出的条件单**（`order_type` = take_profit / stop_loss，
 * `status` = pending，开仓时由引擎 `_upsert(...)` 挂出，触发即市价平仓）。
 *
 * 四条断言，缺一条都会漏掉一类退化：
 * 1. **旧模块必须消失**：页面不得再出现「手动下单」（否则等于没删干净）。
 * 2. **新面板必须在位**：标题「挂单情况 (N)」可见。
 * 3. **数字必须与后端一致**：面板计数 == 聚合端点里 `status==='pending'` 的条数
 *    （防止"只渲染了前几条"或"把已成交也当成挂单"这类静默偏差）。
 * 4. **单子必须可读**：至少渲染出 `止盈`/`止损` 标签与「触发」价格行。
 *
 * 会话：注入本地假 token（后端对回环请求走本地租户通道），沿用 paper-dashboard-aggregate.spec.ts 的做法。
 * 前置：后端 :8000 与前端 dev :5273 已运行。
 */
import { test, expect } from "@playwright/test";

const NS_KEY = "arena_last_paper_account_id";
const ACCOUNT_ID = "14";

test.describe("模拟盘页：挂单情况面板", () => {
  test.describe.configure({ timeout: 120_000 });

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

  test("旧「手动下单」已移除、新「挂单情况」计数与后端一致", async ({ page, request }) => {
    // 后端真值（聚合端点与页面同源）
    const resp = await request.get("http://127.0.0.1:8000/api/paper/dashboard/14");
    expect(resp.ok(), "聚合端点应可用").toBeTruthy();
    const payload = (await resp.json()) as { orders?: Array<{ status?: string }> };
    const pendingCount = (payload.orders ?? []).filter((o) => o.status === "pending").length;

    await page.goto("/paper-trading", { waitUntil: "domcontentloaded", timeout: 40_000 });

    const panel = page.getByText(/挂单情况/).first();
    await expect(panel, "新面板必须出现").toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText("手动下单"),
      "旧「手动下单」必须已从页面移除",
    ).toHaveCount(0);

    // 计数一致（后端 pending 数 == 标题里的 N）
    const heading = page.getByRole("heading", { name: /挂单情况/ }).first();
    await expect(heading).toContainText(`(${pendingCount})`);

    if (pendingCount > 0) {
      await expect(page.getByText("触发", { exact: false }).first()).toBeVisible();
      const badges = await page.getByText(/^(止盈|止损)$/).count();
      expect(badges, "每张挂单都应带止盈/止损标签").toBeGreaterThan(0);
      // [2026-09-23 防回归] 曾经用 max-h-[320px] 固定截断：10 张单只露 5 张、下方留大片空白。
      // 两条断言钉住：① DOM 里必须**渲染全部**挂单（不是只渲染前几条）；
      // ② 列表容器不得再带固定高度上限（否则小屏/多单时会被截断）。
      const rows = await page.getByTestId("pending-orders-list").locator("> div").count();
      expect(rows, `挂单应全部渲染：后端 ${pendingCount} 张，DOM ${rows} 条`).toBe(pendingCount);
      const cls = (await page.getByTestId("pending-orders-list").getAttribute("class")) || "";
      expect(cls, "列表容器不应再有固定高度上限（max-h-[...]）").not.toMatch(/max-h-\[/);
      expect(cls, "列表容器应占满卡片（flex-1 min-h-0）").toMatch(/flex-1/);
    } else {
      await expect(page.getByText("当前无挂单")).toBeVisible();
    }

    // [2026-09-23 对齐回归 v2] 左列现在是**上下两卡**（挂单 + 收益曲线），行高由右侧持仓卡决定：
    // 断言改为"左列整体与持仓卡等高对齐"+"左列两张卡不重叠、不溢出容器"。
    const leftCol = await page.getByTestId("left-column").boundingBox();
    const leftCard = await page.getByTestId("pending-orders-card").boundingBox();
    const curveCard = await page.getByTestId("equity-series-card").boundingBox();
    const right = await page.getByTestId("open-positions-card").boundingBox();
    expect(leftCol && right, "左列与持仓卡的盒模型都必须可测").toBeTruthy();
    if (leftCol && right && leftCard && curveCard) {
      expect(Math.abs(leftCol.y - right.y), `左列与持仓卡顶边应对齐（Δ=${Math.abs(leftCol.y - right.y)}px）`).toBeLessThanOrEqual(1);
      expect(
        Math.abs(leftCol.height - right.height),
        `左列与持仓卡应等高（左 ${leftCol.height}px / 右 ${right.height}px）——不等高就是"外框被延长"`,
      ).toBeLessThanOrEqual(1);
      // 两张卡都在左列内：首卡顶部 ≥ 列顶，末卡底部 ≤ 列底
      expect(leftCard.y, "挂单卡不应溢出左列顶部").toBeGreaterThanOrEqual(leftCol.y - 1);
      const curveBottom = curveCard.y + curveCard.height;
      expect(curveBottom, `收益曲线卡不应溢出左列底部（卡底 ${curveBottom} vs 列底 ${leftCol.y + leftCol.height}）`).toBeLessThanOrEqual(
        leftCol.y + leftCol.height + 1,
      );
      // 两张卡不重叠
      expect(curveCard.y, "两卡不得重叠").toBeGreaterThanOrEqual(leftCard.y + leftCard.height - 1);
    }

    // 新卡片必须真的在跑：有 SVG、有当前权益数值、口径切换可用
    await expect(page.getByTestId("equity-svg")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("equity-current")).toContainText("$");
    await page.getByTestId("equity-mode-total").click();
    await expect(page.getByTestId("equity-mode-total")).toHaveClass(/border-cyan-400/);
    await page.getByTestId("equity-mode-realized").click();

    // [2026-09-23 用户要求] 当前持仓**不滚动、10 个槽位全部显示**：表格容器不得出现纵向溢出。
    // （挂单卡允许在框内滚动——挂单数最多是槽位 ×2。）
    const posScroll = await page
      .getByTestId("open-positions-scroll")
      .evaluate((el) => ({ clientH: el.clientHeight, scrollH: el.scrollHeight }));
    expect(
      posScroll.scrollH - posScroll.clientH,
      `当前持仓不应纵向滚动（应全部显示）：clientH=${posScroll.clientH} scrollH=${posScroll.scrollH}`,
    ).toBeLessThanOrEqual(1);
  });
});
