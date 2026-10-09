/**
 * [F246d] /arbitrage/risk 页面回归探针
 * 断言：
 *  R1 页面渲染干净（无 pageerror / 无错误边界）
 *  R2 出现「L1 专属账户 · 做市敞口」卡片
 *  R3 卡片内出现 账户权益 / 做市名义 / 做市占比 / 可用保证金 四个数字
 *  R4 不再出现旧视图：资金池 / 策略预算占用 / 策略资金占用 / 车道占用
 *  R5 熔断矩阵与组合级熔断演练卡片仍在
 *
 * 用法：node e2e/probe-risk-page.js
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

(async () => {
  let browser = null;
  for (const channel of ["msedge", "chrome", "chromium"]) {
    try {
      browser = await chromium.launch({ channel, headless: true });
      break;
    } catch { /* 下一个 */ }
  }
  if (!browser) { console.log("✗ 没有可用浏览器"); process.exit(2); }

  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1400 } });
  await ctx.addInitScript(([token, user]) => {
    try {
      localStorage.setItem("arena_access_token", token);
      localStorage.setItem("arena_refresh_token", token);
      localStorage.setItem("arena_has_session", "1");
      localStorage.setItem("arena_auth_user", JSON.stringify(user));
    } catch { /* ignore */ }
  }, [TOKEN, USER]);

  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`${BASE}/arbitrage/risk`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(6500);
  const text = await page.evaluate(() => document.body.innerText);

  const hasErrBoundary = /此页面渲染出错/.test(text);
  const checks = [];
  const check = (id, ok, extra = "") => {
    checks.push({ id, ok, extra });
    console.log(`${ok ? "✓" : "✗"} ${id}${extra ? ` — ${extra}` : ""}`);
  };

  check("R1", errors.length === 0 && !hasErrBoundary, errors.length ? errors[0] : "");
  check("R2", text.includes("L1 专属账户 · 做市敞口"));
  check("R3", ["账户权益", "做市名义", "做市占比", "可用保证金"].every((k) => text.includes(k)));
  check("R4", !/(资金池|策略预算占用|策略资金占用|车道占用)/.test(text));
  check("R5", text.includes("熔断矩阵") && text.includes("组合级熔断演练"));

  const failed = checks.filter((c) => !c.ok);
  await browser.close();
  console.log(`\n风险页断言：${checks.length} 条，${failed.length} 条失败`);
  process.exit(failed.length ? 1 : 0);
})();
