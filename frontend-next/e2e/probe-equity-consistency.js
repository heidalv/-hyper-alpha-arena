const { chromium } = require("playwright");
const BASE = process.env.PROBE_BASE || "http://127.0.0.1:8000";
const OUT = "D:/001Alpha/Hyper-Alpha-Arena/logs";
function b64url(o) { return Buffer.from(JSON.stringify(o)).toString("base64url"); }
const TOKEN = [b64url({ alg: "none", typ: "JWT" }),
  b64url({ sub: "1", id: 1, username: "probe", email: "probe@local", role: "admin",
           exp: Math.floor(Date.now() / 1000) + 86400 }), "sig"].join(".");
const USER = { id: 1, username: "probe", email: "probe@local", role: "admin" };
const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

(async () => {
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await ctx.addInitScript(([t, u]) => {
    localStorage.setItem("arena_access_token", t);
    localStorage.setItem("arena_refresh_token", t);
    localStorage.setItem("arena_has_session", "1");
    localStorage.setItem("arena_auth_user", JSON.stringify(u));
  }, [TOKEN, USER]);
  const page = await ctx.newPage();
  await page.goto(BASE + "/arbitrage", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(14000);
  const kpi = norm(await page.locator("main .grid").first().innerText());
  const card = norm(await page.locator("#unified-account").innerText());
  console.log("=== 顶栏 KPI ===");
  console.log(kpi.slice(0, 220));
  console.log("=== 账户卡（前 400 字）===");
  console.log(card.slice(0, 400));
  const top = (kpi.match(/\$[\d,]+\.\d{2}/) || [])[0];
  const acctEq = (card.match(/账户权益（分配资本 \+ 已实现盈亏） \$[\d,]+\.\d{2}/) || [])[0];
  console.log("=> 顶栏:", top, "| 账户权益:", acctEq);
  await page.screenshot({ path: `${OUT}/_shot_after_fix.png`, fullPage: false });
  await browser.close();
})();
