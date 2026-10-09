/** 诊断：Playwright 打开模拟盘页时，实际落在哪个 URL、发了哪些请求。 */
import { chromium } from "playwright";

const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5273";
const browser = await chromium.launch();
const page = await browser.newPage();
const hosts = new Map();
const api = [];
page.on("request", (r) => {
  const u = new URL(r.url());
  hosts.set(u.host, (hosts.get(u.host) || 0) + 1);
  if (u.pathname.startsWith("/api")) api.push(`${r.method()} ${u.host}${u.pathname}`);
});
page.on("console", (m) => { if (m.type() === "error") console.log("  [console error]", m.text().slice(0, 160)); });
page.on("pageerror", (e) => console.log("  [pageerror]", String(e).slice(0, 200)));

await page.goto(BASE + "/paper-trading", { waitUntil: "domcontentloaded", timeout: 30_000 });
await page.waitForTimeout(6000);
console.log("最终 URL        :", page.url());
console.log("标题            :", await page.title());
console.log("localStorage 键 :", await page.evaluate(() => Object.keys(window.localStorage)));
for (const k of ["arena_last_paper_account_id", "arena_access_token", "arena_refresh_token", "arena_backend_url"]) {
  console.log(`  ${k.padEnd(30)} =`, await page.evaluate((kk) => window.localStorage.getItem(kk), k));
}
console.log("请求 host 统计  :", [...hosts.entries()]);
console.log("API 请求        :", api.length ? api.slice(0, 20) : "（无）");
const text = (await page.textContent("body")) || "";
console.log("页面文本前 200 字:", text.replace(/\s+/g, " ").slice(0, 200));
await browser.close();
