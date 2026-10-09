const { chromium } = require("playwright");
const BASE = "http://127.0.0.1:8000";
function b64url(o) { return Buffer.from(JSON.stringify(o)).toString("base64url"); }
const TOKEN = [b64url({ alg: "none", typ: "JWT" }),
  b64url({ sub: "1", id: 1, username: "probe", email: "probe@local", role: "admin",
           exp: Math.floor(Date.now() / 1000) + 86400 }), "sig"].join(".");
const USER = { id: 1, username: "probe", email: "probe@local", role: "admin" };

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1700, height: 1600 } });
  await ctx.addInitScript(([t, u]) => {
    localStorage.setItem("arena_access_token", t);
    localStorage.setItem("arena_refresh_token", t);
    localStorage.setItem("arena_has_session", "1");
    localStorage.setItem("arena_auth_user", JSON.stringify(u));
  }, [TOKEN, USER]);
  const page = await ctx.newPage();
  await page.goto(BASE + "/arbitrage/lanes?lane=mm_asterdex", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(9000);
  const body = (await page.locator("body").innerText()).replace(/\s+/g, " ").trim();
  console.log("交易看板:", body.includes("交易看板") ? "✓" : "✗");
  console.log("深度说明(10 档):", body.includes("10 档深度") ? "✓" : "✗");
  console.log("我方标注:", (body.match(/我方/g) || []).length, "处");
  // 深度阶梯：检查 BTC 卡内出现连续价格行（第一档买卖价）
  const seg = body.slice(body.indexOf("交易看板"), body.indexOf("交易看板") + 900);
  console.log("片段:", seg.slice(0, 600));
  await page.screenshot({ path: "D:/001Alpha/Hyper-Alpha-Arena/logs/_shot_board_depth.png", fullPage: false });
  await browser.close();
})();
