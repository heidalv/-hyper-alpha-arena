// 单页校验（第三轮打磨）：12-intel.html 标签平衡 / id 唯一 / data-* 引用 / 第三层组件清单
// 用法: node tools/_verify-intel.js
const fs = require("fs");
const path = require("path");

const file = path.join(__dirname, "..", "pages", "12-intel.html");
const src = fs.readFileSync(file, "utf8");
let fail = false;

// 1. 标签平衡（忽略注释 / void / self-closed）
const noCmt = src.replace(/<!--[\s\S]*?-->/g, "").replace(/<[a-zA-Z][^>]*\/\s*>/g, "");
const VOID = new Set(["meta", "link", "img", "input", "br", "hr", "source", "wbr", "area", "base", "col", "embed", "track", "param", "!doctype"]);
const count = {};
for (const m of noCmt.matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)(?=[\s>])/g)) {
  const t = m[1].toLowerCase();
  if (!VOID.has(t)) count[t] = (count[t] || 0) + 1;
}
for (const m of noCmt.matchAll(/<\/([a-zA-Z][a-zA-Z0-9-]*)>/g)) {
  const t = m[1].toLowerCase();
  count[t] = (count[t] || 0) - 1;
}
const badTags = Object.entries(count).filter(([, n]) => n !== 0);
if (badTags.length) fail = true;
console.log("1) 标签平衡:", badTags.length ? "FAIL → " + badTags.map(([t, n]) => t + (n > 0 ? "+" : "") + n).join(" ") : "OK ✅");

// 2. id 唯一性
const ids = [...src.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);
const dup = [...new Set(ids.filter((v, i) => ids.indexOf(v) !== i))];
if (dup.length) fail = true;
console.log("2) id 唯一:", ids.length + " 个 id", dup.length ? "FAIL → 重复: " + dup.join(",") : "OK ✅");

// 3. data-modal / data-drawer / data-menu 引用有效性
for (const attr of ["data-modal", "data-drawer", "data-menu"]) {
  const refs = [...src.matchAll(new RegExp(attr + '="([^"]+)"', "g"))].map((m) => m[1]);
  const missing = [...new Set(refs.filter((r) => !ids.includes(r)))];
  if (missing.length) fail = true;
  console.log("3) " + attr + " 引用:", refs.length + " 处", missing.length ? "FAIL → 缺失 id: " + missing.join(",") : "OK ✅");
}

// 4. 第三层组件清单（本轮打磨验收项）
const need = {
  "count-up 数字滚动": (src.match(/class="[^"]*count-up/g) || []).length,
  "cd-auto 自动倒计时": (src.match(/cd-auto/g) || []).length,
  "lag 延迟指示": (src.match(/class="lag"/g) || []).length,
  "kwrap 十字线容器": (src.match(/class="kwrap"/g) || []).length,
  "kline-hint 悬浮提示": (src.match(/kline-hint/g) || []).length,
  "sortable 排序表头": (src.match(/sortable/g) || []).length,
  "sorted 激活排序": (src.match(/sorted/g) || []).length,
  "pager 分页器": (src.match(/pager/g) || []).length,
  "tfoot 汇总行": (src.match(/<tfoot>/g) || []).length,
  "mini-seg 指标行": (src.match(/mini-seg/g) || []).length,
  "input-group 搜索": (src.match(/input-group/g) || []).length,
  "盘口价差": src.includes("盘口价差") ? 1 : 0,
  "深度曲线": (src.match(/深度曲线/g) || []).length,
  "最近成交": (src.match(/最近成交/g) || []).length,
  "flash-up 最新行": (src.match(/flash-up/g) || []).length,
  "多空比进度条": (src.match(/多空比/g) || []).length,
  "下单按钮": src.includes(">下单</button>") ? 1 : 0,
  "标记价/指数价": (src.match(/标记价|指数价/g) || []).length,
};
console.log("4) 第三层组件清单:", JSON.stringify(need));

// 5. 红线
const red = {
  "无 <script>": !/<script/i.test(src),
  "无 html/head/body": !/<html|<head|<body/i.test(src),
  "含 <section": /<section/i.test(src),
};
if (!Object.values(red).every(Boolean)) fail = true;
console.log("5) 红线:", JSON.stringify(red));

console.log(fail ? "RESULT: FAIL" : "RESULT: ALL PASS");
