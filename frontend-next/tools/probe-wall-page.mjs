/** 诊断画布页（2026-10-01 合并后为 /agent-monitor?tab=wall）：注入本地会话后打印 URL / 请求 / 控制台错误 / 正文片段。 */
import { chromium } from "playwright";

const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5273";
const browser = await chromium.launch();
const ctx = await browser.newContext();
await ctx.addInitScript(() => {
  try {
    localStorage.setItem("arena_access_token", "e2e-dummy");
    localStorage.setItem("arena_refresh_token", "e2e-dummy");
    localStorage.setItem("arena_has_session", "1");
  } catch { /* ignore */ }
});
const page = await ctx.newPage();
const api = [];
page.on("request", (r) => {
  const u = new URL(r.url());
  if (u.pathname.startsWith("/api")) api.push(`${u.pathname}${u.search}`.slice(0, 90));
});
page.on("console", (m) => { if (m.type() === "error") console.log("  [console error]", m.text().slice(0, 300)); });
page.on("pageerror", (e) => console.log("  [pageerror]", String(e).slice(0, 400)));

await page.goto(BASE + "/agent-monitor?tab=wall", { waitUntil: "domcontentloaded", timeout: 30_000 });
await page.waitForTimeout(9000);
console.log("最终 URL   :", page.url());
console.log("API 请求   :", api.length ? api.slice(0, 12) : "（无）");
const text = (await page.textContent("body")) || "";
console.log("正文前 400 :", text.replace(/\s+/g, " ").slice(0, 400));
console.log("按钮:", await page.locator("button").allTextContents());
await browser.close();
