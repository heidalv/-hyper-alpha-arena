/**
 * 车道页渲染探针（回归守卫）：用真实浏览器加载页面，任何 pageerror / console.error 都算失败。
 *
 * [F195 2026-09-15] 为什么要这个脚本
 * --------------------------------------------------
 * `/arbitrage/lanes` 曾**刷新即崩**，但源码、预渲染 HTML、接口字段三项静态检查**全部通过** ✗
 * —— 因为真实原因（`fmtNum(null)` ⇒ `null.toLocaleString()` 抛错）只在**浏览器渲染**时才暴露，
 * 而 React 把 DOM 迁移失败包装成 `NotFoundError: insertBefore ...`，
 * **报错文案指向 DOM，与根因（缺值格式化）完全无关** ✗✗。
 * 所以这类缺陷必须有一个"真渲染一次、抓 console"的守卫 ✓ —— 静态审计替代不了它 ✓。
 *
 * 认证：AuthGate 只校验**本地**的 user 缓存与 token 存在性（`syncBootstrap` 不做服务端校验，
 * 见 `lib/stores/auth.ts:148-169`），而本机 API 在 loopback 上不要求鉴权 ⇒ 注入一份伪造会话
 * 即可进入业务页，**不需要真实账号、不写数据库、不触发任何下单** ✓。
 *
 * 用法：
 *   node e2e/probe-lane-render.js                 # 列表页 + 当前所有车道的详情页
 *   node e2e/probe-lane-render.js <url> [...]     # 指定 URL
 * 退出码：0 = 全部干净；1 = 有页面报错；2 = 没有可用浏览器。
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

/** 探针必须忽略的噪声：Next 预取被中止（正常行为，非错误）。 */
function isNoise(text) {
  return /net::ERR_ABORTED/.test(text);
}

/**
 * 浏览器把"资源返回 4xx"也打成 console.error ⇒ 会把**业务上正常的**状态码
 * （如车道未激活返回 409）算成渲染失败 ✗ —— 探针要抓的是**渲染**错误，不是 HTTP 语义。
 * 分类：pageerror / 错误边界 / React 的渲染报错 ⇒ 失败；
 *      4xx ⇒ 仅提示（业务状态）；5xx ⇒ 失败（服务端真出错）。
 */
function classify(text) {
  const m = text.match(/status of (\d{3})/);
  if (m) {
    const code = Number(m[1]);
    return code >= 500 ? "fail" : "warn";
  }
  return "fail";
}

async function laneIds() {
  try {
    const r = await fetch(`${BASE}/api/trading/lanes`);
    const j = await r.json();
    return (j.items || []).map((x) => x.lane_id).filter(Boolean);
  } catch {
    return [];
  }
}

(async () => {
  const argv = process.argv.slice(2);
  let urls = argv;
  if (!urls.length) {
    const ids = await laneIds();
    urls = [`${BASE}/arbitrage/lanes`,
            ...ids.map((id) => `${BASE}/arbitrage/lanes?lane=${encodeURIComponent(id)}`)];
  }

  let browser = null;
  for (const channel of ["msedge", "chrome", "chromium"]) {
    try {
      browser = await chromium.launch({ channel, headless: true });
      console.log(`[launch] channel=${channel}`);
      break;
    } catch (e) {
      console.log(`[launch] ${channel} 不可用: ${String(e.message).split("\n")[0]}`);
    }
  }
  if (!browser) { console.log("✗ 没有可用浏览器"); process.exit(2); }

  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1200 } });
  await ctx.addInitScript(([token, user]) => {
    try {
      localStorage.setItem("arena_access_token", token);
      localStorage.setItem("arena_refresh_token", token);
      localStorage.setItem("arena_has_session", "1");
      localStorage.setItem("arena_auth_user", JSON.stringify(user));
    } catch { /* ignore */ }
  }, [TOKEN, USER]);

  let failed = 0;
  let warned = 0;
  for (const url of urls) {
    const page = await ctx.newPage();
    const problems = [];
    const warns = [];
    page.on("pageerror", (e) => problems.push(
      `[pageerror] ${e.name}: ${e.message}\n` +
      (e.stack || "").split("\n").slice(0, 8).map((l) => "      " + l.trim()).join("\n")));
    page.on("console", (m) => {
      if (m.type() !== "error") return;
      const t = m.text();
      if (isNoise(t)) return;
      (classify(t) === "fail" ? problems : warns).push(`[console.error] ${t}`);
    });
    page.on("response", (r) => {
      const s = r.status();
      if (s >= 400) warns.push(`[http ${s}] ${r.url()}`);
    });

    let reached = "";
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await page.waitForTimeout(6000);
      reached = page.url();
      // 错误边界是否被触发（文案来自 app/error.tsx）
      const boundary = await page.locator("text=此页面渲染出错").count();
      if (boundary > 0) problems.push("[boundary] 命中路由错误边界「此页面渲染出错」");
    } catch (e) {
      problems.push(`[goto] ${e.message}`);
    }
    await page.close();

    if (problems.length) {
      failed += 1;
      console.log(`\n✗ ${url}\n   最终 URL: ${reached}`);
      problems.forEach((p) => console.log("   " + p));
      warns.forEach((p) => console.log("   (提示) " + p));
    } else if (warns.length) {
      warned += 1;
      console.log(`~ ${url}（渲染正常，有 ${warns.length} 条 HTTP 提示）`);
      warns.slice(0, 4).forEach((p) => console.log("   (提示) " + p));
    } else {
      console.log(`✓ ${url}`);
    }
  }

  await browser.close();
  console.log(`\n${failed ? "✗" : "✓"} 探针结果：${urls.length} 个页面，`
    + `${failed} 个渲染失败，${warned} 个仅 HTTP 提示`);
  process.exit(failed ? 1 : 0);
})();
