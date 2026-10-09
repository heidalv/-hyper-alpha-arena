/**
 * 账户页可读性探针（F221 回归守卫）
 *
 * 用户原话：「重置账户了，怎么可用显示还是旧的，也不是新的 300 账户，显示没有主次，
 *            我根本就不知道现在账户有多少钱，是实盘，还是模拟账户。完全是混乱的」
 *
 * 本探针把这句话变成**可自动判定的断言**（每一条都对应一个真实缺陷，不是审美）：
 *   A1 渲染干净：无 pageerror / 无错误边界 / 无 5xx。
 *   A2 盘别可见：`/arbitrage` 与卡片里都出现「模拟盘」字样。
 *   A3 只有一个主数字：账户卡里大号（text-3xl）数字恰为 1 个，且等于
 *      `account.equity`（= total_equity + realized_pnl）的渲染值 `$X.XX`。
 *   A4 口径自洽：账户卡的 可用/冻结/分配资本 三项与 `/api/trading/account/unified`
 *      完全一致（**这条专门防"可用显示还是旧的"** —— 曾出现账户头 $300 而
 *      分所行 $224.85 同页并存）。
 *   A5 无裸「可用」：所有"可用"都必须带限定词（可用保证金 / 本所可用），
 *      避免同页两个都叫「可用」的不同数字。
 *   A6 时代隔离：本时代表与"含重置前历史"的折叠块同时存在且标注清楚。
 *
 * 用法：node e2e/probe-account-clarity.js
 * 退出码：0 = 全部通过；1 = 有断言失败；2 = 没有可用浏览器。
 */
const { chromium } = require("playwright");

const BASE = process.env.PROBE_BASE || "http://127.0.0.1:8000";

function b64url(o) {
  return Buffer.from(JSON.stringify(o)).toString("base64url");
}
const TOKEN = [
  b64url({ alg: "none", typ: "JWT" }),
  b64url({ sub: "1", id: 1, username: "probe", email: "probe@local", role: "admin",
           exp: Math.floor(Date.now() / 1000) + 86400 }),
  "sig",
].join(".");
const USER = { id: 1, username: "probe", email: "probe@local", role: "admin" };

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "✓" : "✗"} ${name}${detail ? " — " + detail : ""}`);
}

const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
const usd = (v) => (typeof v === "number" && Number.isFinite(v) ? `$${v.toFixed(2)}` : null);

(async () => {
  let browser = null;
  for (const channel of ["msedge", "chrome", "chromium"]) {
    try {
      browser = await chromium.launch({ channel, headless: true });
      break;
    } catch { /* 下一个 */ }
  }
  if (!browser) { console.log("✗ 没有可用浏览器"); process.exit(2); }

  // 后端真值（断言 A3/A4 的对照）
  let acct = null;
  try {
    const j = await (await fetch(`${BASE}/api/trading/account/unified`)).json();
    acct = j.account;
  } catch (e) {
    console.log(`✗ 无法读取 /api/trading/account/unified: ${e.message}`);
  }

  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1400 } });
  await ctx.addInitScript(([token, user]) => {
    try {
      localStorage.setItem("arena_access_token", token);
      localStorage.setItem("arena_refresh_token", token);
      localStorage.setItem("arena_has_session", "1");
      localStorage.setItem("arena_auth_user", JSON.stringify(user));
    } catch { /* ignore */ }
  }, [TOKEN, USER]);

  for (const path of ["/arbitrage", "/arbitrage/lanes"]) {
    const page = await ctx.newPage();
    const problems = [];
    page.on("pageerror", (e) => problems.push(`[pageerror] ${e.name}: ${e.message}`));
    page.on("console", (m) => {
      if (m.type() !== "error") return;
      const t = m.text();
      if (/net::ERR_ABORTED/.test(t)) return;
      const code = (t.match(/status of (\d{3})/) || [])[1];
      if (code && Number(code) < 500) return;
      problems.push(`[console.error] ${t}`);
    });

    await page.goto(BASE + path, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await page.waitForTimeout(6500);

    const boundary = await page.locator("text=此页面渲染出错").count();
    check(`A1 ${path} 渲染干净`, problems.length === 0 && boundary === 0,
      problems.length ? problems.join(" | ").slice(0, 400) : "无 pageerror / 无错误边界");

    const body = norm(await page.locator("body").innerText());

    if (path === "/arbitrage") {
      // A2 盘别可见
      check("A2 页面出现「模拟盘」标识", body.includes("模拟盘"),
        `出现 ${(body.match(/模拟盘/g) || []).length} 次`);

      const card = page.locator("#unified-account");
      check("A2b 统一模拟账户卡片存在", (await card.count()) === 1);
      const cardText = (await card.count()) ? norm(await card.innerText()) : "";

      // A3 唯一主数字 = account.equity
      const big = card.locator(".text-3xl");
      const bigCount = await big.count();
      const bigTexts = [];
      for (let i = 0; i < bigCount; i++) bigTexts.push(norm(await big.nth(i).innerText()));
      const wantEq = acct ? usd(acct.equity) : null;
      check("A3 账户卡只有一个大号主数字", bigCount === 1,
        `找到 ${bigCount} 个: ${JSON.stringify(bigTexts)}`);
      check("A3b 主数字 = 后端 account.equity", wantEq != null && bigTexts[0] === wantEq,
        `渲染 ${JSON.stringify(bigTexts[0])} vs 后端 ${wantEq}`);

      // A4 三项次级读数与后端一致（专门防"可用显示还是旧的"）
      if (acct) {
        const pairs = [
          ["可用保证金", acct.available_balance],
          ["冻结", acct.frozen_balance],
          ["分配资本", acct.total_equity],
        ];
        for (const [label, want] of pairs) {
          const got = usd(want);
          // 账户头次级指标 + 分所行里都应能找到同一个数值
          check(`A4 ${label} 渲染值 = ${got}`, got != null && cardText.includes(got),
            cardText.includes(got) ? "" : `卡片文本中找不到 ${got}\n      卡片文本: ${cardText.slice(0, 700)}`);
        }
      }

      // A5 没有裸「可用」（必须带限定词；[F246b] 分所行已删除，只剩「可用保证金」）
      const bare = (cardText.match(/可用/g) || []).length;
      const qualified = (cardText.match(/可用保证金/g) || []).length;
      check("A5 所有「可用」都带限定词（无裸可用）", bare === qualified,
        `「可用」${bare} 处 / 限定词 ${qualified} 处`);

      // A6 时代隔离（[F246b] 简化为"时代内"单一口径，废除分账表/分所表）
      check("A6 有「时代内已实现」口径且标注时代起点",
        cardText.includes("时代内已实现") && cardText.includes("自 "),
        cardText.includes("时代内已实现") ? "" : "未见时代内口径");

      // A6b [F246b] 废除旧资金分配：卡片不得再出现 分账表/分所表/预算 字样
      check("A6b 无旧资金分配字样（按策略分账/交易所分户/预算）",
        !/(按策略分账|交易所分户|预算)/.test(cardText),
        cardText.slice(0, 300));

      // 顶栏组合权益
      const eqTop = acct ? usd(acct.equity) : null;
      if (eqTop) {
        const kpiText = norm(await page.locator("main, body").first().innerText());
        check("A7 页面同时出现后端权益值（顶栏与卡片一致）", kpiText.includes(eqTop), eqTop);
      }

      // [F226] A8 「组合权益」必须与「账户权益」是**同一个数**。
      // 现场（用户截图）：组合权益 $300.00 与账户权益 $300.93 并存 ✗
      // 根因：/portfolio/summary 只读 total_equity，未加 realized_pnl。
      const kpiCard = page.locator("main .grid").first();
      const topText = norm(await kpiCard.innerText());
      const topEq = (topText.match(/\$[\d,]+\.\d{2}/) || [])[0] || null;
      check("A8 组合权益 == 账户权益（同页不得出现两个权益数）",
        topEq != null && eqTop != null && topEq === eqTop,
        `组合权益 ${topEq} vs 账户权益 ${eqTop}`);
    }

    await page.close();
  }

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${failed.length ? "✗" : "✓"} 可读性断言：${results.length} 条，`
    + `${failed.length} 条失败`);
  failed.forEach((f) => console.log(`   ✗ ${f.name} — ${f.detail}`));
  process.exit(failed.length ? 1 : 0);
})();
