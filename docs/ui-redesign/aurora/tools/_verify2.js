// 二级视图全站验证：id 唯一性 / data-* 引用有效性 / 二级组件统计
const fs = require("fs");
const src = fs.readFileSync("D:/001Alpha/Hyper-Alpha-Arena/docs/ui-redesign/aurora/index.html", "utf8");

// 1. 全部 id 唯一性
const ids = [...src.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
const dupIds = [...new Set(ids.filter((v, i) => ids.indexOf(v) !== i))];
console.log("全站 id 数:", ids.length, dupIds.length ? "重复: " + dupIds.join(",") : "无重复 ✅");

// 2. data-modal / data-drawer / data-menu 引用有效性
let bad = [];
for (const attr of ["data-modal", "data-drawer", "data-menu"]) {
  const refs = [...src.matchAll(new RegExp(attr + '="([^"]+)"', "g"))].map(m => m[1]);
  const missing = [...new Set(refs.filter(r => !ids.includes(r)))];
  console.log(attr + " 引用:", refs.length, missing.length ? "缺失 id: " + missing.join(",") : "全部有效 ✅");
  if (missing.length) bad.push(attr);
}

// 3. 二级组件统计
const stat = {
  "弹窗 .mask": (src.match(/class="mask"/g) || []).length,
  "抽屉 .drawer": (src.match(/class="drawer[ "]/g) || []).length,
  "下拉 .menu": (src.match(/class="menu"/g) || []).length,
  "data-confirm 确认框": (src.match(/data-confirm=/g) || []).length,
  "data-toast 提示": (src.match(/data-toast=/g) || []).length,
  "向导步骤": (src.match(/wizard-steps/g) || []).length,
  "开关 .switch": (src.match(/class="switch/g) || []).length,
  "tab-pane": (src.match(/class="tab-pane"/g) || []).length,
  "行内 tab 切换 onclick": (src.match(/onclick="document.querySelectorAll\('\.tab-pane'\)/g) || []).length,
};
console.log("二级组件统计:", JSON.stringify(stat, null, 0));

// 4. 关键交互页面抽查
const checks = {
  "命令面板": src.includes('id="cmdMask"') && src.includes("Ctrl K"),
  "通知中心": src.includes('id="notifPanel"'),
  "确认框机制": src.includes("UI.confirm"),
  "intel 单币种抽屉": src.includes('id="d-market"'),
  "智能学习 7 pane": (src.match(/id="pane-/g) || []).length >= 7,
  "创建会话弹窗": src.includes('id="m-create-session"'),
  "交易所向导": src.includes('id="m-wizard"'),
  "日志详情": src.includes('id="m-log-detail"'),
  "策略详情抽屉": src.includes('id="d-strategy"'),
  "报错详情": src.includes('id="m-err"'),
};
const fail = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
console.log("关键交互抽查:", fail.length ? "缺: " + fail.join(",") : "全部通过 ✅");
console.log(bad.length ? "RESULT: 存在无效引用" : "RESULT: ALL PASS");
