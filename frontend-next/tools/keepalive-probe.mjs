/**
 * F5（keep-alive）受控测量：只开一个页面、保持 100 秒，让页面自身轮询跑起来。
 * 期间不发任何其它请求，之后从后端访问日志统计该时间窗内的客户端端口复用率。
 * 用法：node tools/keepalive-probe.mjs
 */
import { chromium } from "playwright";

const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5273";
const HOLD_MS = Number(process.env.HOLD_MS || 100_000);

const browser = await chromium.launch();
const ctx = await browser.newContext();
await ctx.addInitScript(() => {
  try {
    localStorage.setItem("arena_access_token", "e2e-dummy");
    localStorage.setItem("arena_refresh_token", "e2e-dummy");
    localStorage.setItem("arena_has_session", "1");
    localStorage.setItem("arena_last_paper_account_id", "14");
  } catch {
    /* ignore */
  }
});
const page = await ctx.newPage();
/** 只统计**本页面**发出的请求（后端访问日志里可能混有其他客户端，不可用于对比）。 */
const byPath = new Map();
page.on("request", (r) => {
  const u = r.url();
  if (!u.includes(":8000/api/")) return;
  const p = new URL(u).pathname.replace(/\/(balance|positions|orders|summary|dashboard)\/\d+/, "/$1/{id}");
  byPath.set(p, (byPath.get(p) || 0) + 1);
});

const t0 = new Date();
await page.goto(BASE + "/paper-trading", { waitUntil: "domcontentloaded", timeout: 30_000 });
console.log(`开始时间 ${t0.toISOString()}（本地 ${t0.toLocaleTimeString("zh-CN")}），保持 ${HOLD_MS / 1000}s`);
await page.waitForTimeout(HOLD_MS);
const t1 = new Date();
const total = [...byPath.values()].reduce((a, b) => a + b, 0);
console.log(`结束时间 ${t1.toISOString()}（本地 ${t1.toLocaleTimeString("zh-CN")}）`);
console.log(`本页面共发出 ${total} 个 API 请求（100s 窗口，仅本页）`);
console.log("按端点（仅本页面）：");
for (const [p, n] of [...byPath.entries()].sort((a, b) => b[1] - a[1])) {
  console.log(`  ${p.padEnd(38)} ${String(n).padStart(3)} 次  ≈ 每 ${(HOLD_MS / 1000 / n).toFixed(1)}s 一次`);
}
const paper = [...byPath.entries()]
  .filter(([p]) => p.startsWith("/api/paper/"))
  .reduce((a, [, n]) => a + n, 0);
console.log(`其中 /api/paper/* 合计 ${paper} 次`);
await browser.close();
