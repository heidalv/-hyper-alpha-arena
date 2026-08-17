// 最终验证 aurora/index.html：页面 id 唯一性 / 导航对应 / 关键内容抽查
const fs = require("fs");
const src = fs.readFileSync("D:/001Alpha/Hyper-Alpha-Arena/docs/ui-redesign/aurora/index.html", "utf8");

// 1. 页面 section 检查
const pages = [...src.matchAll(/<section class="page" id="page-([a-z-]+)"/g)].map(m => m[1]);
const dup = pages.filter((p, i) => pages.indexOf(p) !== i);
console.log("页面总数:", pages.length, dup.length ? "重复ID: " + dup.join(",") : "ID 无重复");

// 2. 导航 data-page 与页面 id 对应
const navs = [...src.matchAll(/data-page="([a-z-]+)"/g)].map(m => m[1]);
const missing = navs.filter(n => !pages.includes(n));
const orphan = pages.filter(p => !navs.includes(p));
console.log("导航项:", navs.length, "| 导航无对应页面:", missing.length ? missing.join(",") : "无", "| 页面无导航入口(可用hash直达):", orphan.join(","));

// 3. 重复 id 检查（SVG defs 等）
const ids = [...src.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
const dupIds = ids.filter((v, i) => ids.indexOf(v) !== i);
console.log("全部 id 数:", ids.length, dupIds.length ? "重复: " + [...new Set(dupIds)].join(",") : "无重复");

// 4. 关键内容抽查
const checks = {
  "极光背景": src.includes("aurora-blob"),
  "ticker 7 币种": ["BTC","ETH","SOL","BNB","VIRTUAL","ASTER","XPL"].every(c => src.includes(c)),
  "防御性显示(数据异常)": src.includes("数据异常"),
  "AI决策 SELL≥5": (src.match(/SELL/g) || []).length >= 5,
  "渐变主按钮": src.includes("btn-primary") && src.includes("linear-gradient(135deg, #22D3EE"),
  "glass 卡片": src.includes("class=\"glass"),
  "kpi 渐变数字": src.includes("kpi-value grad"),
  "hash 路由": src.includes("hashchange"),
  "搜索过滤": src.includes("placeholder=\"输入过滤"),
  "状态栏": src.includes("Hyperliquid 已连接"),
  "登录页": src.includes("page-login"),
  "图表中心": src.includes("page-charts"),
  "SVG 蜡烛图": src.includes("viewBox=\"0 0 640 260\""),
};
const fail = Object.entries(checks).filter(([,v]) => !v).map(([k]) => k);
console.log("内容检查:", fail.length ? "缺: " + fail.join(",") : "全部通过 ✅");
