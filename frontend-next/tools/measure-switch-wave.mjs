/**
 * 真浏览器量测：切到模拟盘页时，四个数据请求是否还要等「账户列表」返回。
 *
 * 对应 docs/前端刷新慢根因_20260918.md §1.8 的两级瀑布与 §3 的 F3b 修复。
 * 判定量（都在同一次冷切页里取）：
 *   gap = 第一个 /api/paper/* 请求开始 - /api/account/list 请求开始
 *     - 旧行为（无缓存账户 id）：四个数据 hook `enabled: !!accountId` 必须等列表
 *       ⇒ gap ≈ 账户列表耗时（波2 排在波1 之后）
 *     - 新行为（有缓存账户 id）：波2 与波1 并行 ⇒ gap ≈ 0
 *   ready = 最后一个 /api/paper/* 响应 - 导航开始（"数据齐全"墙钟）
 *
 * 用法（在 frontend-next 下）：node tools/measure-switch-wave.mjs
 */
import { chromium } from "playwright";

const BASE = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5273";
const PAGE = "/paper-trading";
const OTHER = "/factors";
const NS_KEY = "arena_last_paper_account_id";

function newRecorder() {
  const reqs = [];
  return {
    reqs,
    onRequest(req) {
      const u = req.url();
      if (!u.includes(":8000/api/")) return;
      reqs.push({ kind: "req", url: u, t: Date.now(), path: new URL(u).pathname });
    },
    onResponse(res) {
      const u = res.url();
      if (!u.includes(":8000/api/")) return;
      reqs.push({ kind: "res", url: u, t: Date.now(), path: new URL(u).pathname });
    },
  };
}

async function visit(page, rec, path) {
  const t0 = Date.now();
  await page.goto(BASE + path, { waitUntil: "domcontentloaded", timeout: 30_000 });
  // 等 4 个纸面端点都拿到响应（或超时 20s）
  const want = ["/api/paper/balance/", "/api/paper/positions/", "/api/paper/orders/", "/api/paper/summary/"];
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const paths = new Set(rec.reqs.filter((r) => r.kind === "res").map((r) => r.path));
    if (want.every((w) => [...paths].some((p) => p.startsWith(w)))) break;
    await page.waitForTimeout(50);
  }
  return t0;
}

function analyze(rec, t0) {
  const rel = (t) => t - t0;
  const acctReq = rec.reqs.find((r) => r.kind === "req" && r.path === "/api/account/list");
  const paperReqs = rec.reqs.filter((r) => r.kind === "req" && r.path.startsWith("/api/paper/"));
  const paperRes = rec.reqs.filter((r) => r.kind === "res" && r.path.startsWith("/api/paper/"));
  if (!acctReq || !paperReqs.length) return null;
  const firstPaperReq = Math.min(...paperReqs.map((r) => rel(r.t)));
  const lastPaperRes = paperRes.length ? Math.max(...paperRes.map((r) => rel(r.t))) : null;
  const acctRes = rec.reqs.find((r) => r.kind === "res" && r.path === "/api/account/list");
  return {
    gapToWave2: firstPaperReq - rel(acctReq.t),          // 目标量
    accountMs: acctRes ? rel(acctRes.t) - rel(acctReq.t) : null,
    paperFirstMs: firstPaperReq,
    readyMs: lastPaperRes,
    paperCount: paperReqs.length,
  };
}

const browser = await chromium.launch();
const ctx = await browser.newContext();

// ── 本地会话注入（不写库、不伪造签名） ──
// 前端门只看本地 store；后端对回环请求走"本地租户通道"（AUTH_LOCAL_TENANT，
// 无有效凭证时按 admin 注入身份），因此这里给一个**无法解析**的假 token：
//   - isAccessTokenExpiringSoon() 对不可解析 token 返回 false ⇒ 不触发续期；
//   - /auth/me 带假 Bearer → 后端静默落到本地租户通道 → 200 返回真实用户 ⇒ store 放行。
await ctx.addInitScript(() => {
  try {
    localStorage.setItem("arena_access_token", "e2e-dummy");
    localStorage.setItem("arena_refresh_token", "e2e-dummy");
    localStorage.setItem("arena_has_session", "1");
  } catch {
    /* ignore */
  }
});

const page = await ctx.newPage();

// ── 预热：先访问一次，让页面把「上次用过的账户」写进 localStorage ──
const warm = newRecorder();
page.on("request", warm.onRequest);
page.on("response", warm.onResponse);
await visit(page, warm, PAGE);
const cached = await page.evaluate((k) => window.localStorage.getItem(k), NS_KEY);
console.log(`预热完成：localStorage["${NS_KEY}"] = ${cached}`);
await page.goto(BASE + OTHER, { waitUntil: "domcontentloaded" });

// ── A：有缓存账户 id（新行为：波1/波2 并行） ──
const recA = newRecorder();
const pageA = await ctx.newPage();
pageA.on("request", recA.onRequest);
pageA.on("response", recA.onResponse);
const tA = await visit(pageA, recA, PAGE);
const a = analyze(recA, tA);

// ── B：清掉缓存（旧行为：波2 必须等波1） ──
const pageB = await ctx.newPage();
await pageB.goto(BASE + OTHER, { waitUntil: "domcontentloaded" });
await pageB.evaluate((k) => window.localStorage.removeItem(k), NS_KEY);
const recB = newRecorder();
pageB.on("request", recB.onRequest);
pageB.on("response", recB.onResponse);
const tB = await visit(pageB, recB, PAGE);
const b = analyze(recB, tB);

// ── C：再切回有缓存（复现用户"切换模块"） ──
await pageB.evaluate(([k, v]) => window.localStorage.setItem(k, v), [NS_KEY, cached || "14"]);
await pageB.goto(BASE + OTHER, { waitUntil: "domcontentloaded" });
const recC = newRecorder();
const pageC = await ctx.newPage();
pageC.on("request", recC.onRequest);
pageC.on("response", recC.onResponse);
const tC = await visit(pageC, recC, PAGE);
const c = analyze(recC, tC);

const fmt = (x) => (x == null ? "—" : `${Math.round(x)}ms`);
console.log("\n=== 切到 /paper-trading 的请求时序（导航开始 = 0） ===");
console.log("条件                          账户列表耗时   波2首个请求   波2-波1 间隔   数据齐全   纸面请求数");
for (const [label, r] of [["A 有缓存(新行为)", a], ["B 无缓存(旧行为)", b], ["C 有缓存(再切一次)", c]]) {
  if (!r) { console.log(`${label.padEnd(28)} 未能取到完整样本`); continue; }
  console.log(
    `${label.padEnd(28)} ${fmt(r.accountMs).padStart(10)} ${fmt(r.paperFirstMs).padStart(12)} ` +
    `${fmt(r.gapToWave2).padStart(13)} ${fmt(r.readyMs).padStart(10)} ${String(r.paperCount).padStart(11)}`
  );
}
console.log("\n判据：B 的「波2-波1 间隔」≈ 账户列表耗时（串行等待）；A/C 应显著更小（并行）。");

await browser.close();
