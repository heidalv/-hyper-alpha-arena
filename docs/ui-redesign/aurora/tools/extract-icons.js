// 从本地 lucide-react 提取图标 → 生成 aurora/icons.js（供预览用，无网络依赖）
// 用法: node extract-icons.js
const fs = require("fs");
const path = require("path");

const SRC = "D:/001Alpha/Hyper-Alpha-Arena/frontend-next/node_modules/lucide-react/dist/esm/icons";
const OUT = "D:/001Alpha/Hyper-Alpha-Arena/docs/ui-redesign/aurora/icons.js";

// 需要的图标（kebab-case 文件名）
const WANT = [
  "layout-dashboard","brain","coins","radar","flask-conical","trending-up","trending-down",
  "arrow-right-left","zap","activity","server","database","bar-chart-3","workflow","cpu",
  "shield-alert","shield","heart-pulse","settings","file-text","search","bell","refresh-cw",
  "wifi","wifi-off","log-out","log-in","alert-triangle","check-circle-2","x-circle","wallet",
  "clock","timer","gauge","bot","sparkles","filter","arrow-up-down","chevron-down","chevron-right",
  "chevrons-left","plus","minus","x","save","trash-2","pencil","eye","eye-off","download",
  "upload","external-link","layers","git-branch","list-tree","server-cog","play","pause","square",
  "settings-2","key-round","lock","unlock","signal","cable","circuit-board","flame","target",
  "crosshair","telescope","orbit","atom","scan-line","boxes","package-open","hourglass","history",
  "rotate-ccw","rotate-cw","calendar","calendar-days","chart-candlestick","line-chart","pie-chart",
  "bar-chart-4","area-chart","scatter-chart","memory-stick","hard-drive","thermometer","badge-check",
  "badge-alert","info","help-circle","circle-dollar-sign","banknote","receipt","percent",
  "landmark","warehouse","factory","cog","sliders-horizontal","power","rocket","book-open",
  "library","graduation-cap","microscope","waypoints","route","network","share-2","users",
  "user","user-plus","fingerprint","shield-check","shield-x","lightbulb","sun","moon",
  "cloud","cloud-off","plug-zap","battery-charging","kanban","list","grid-3x3","panel-left",
  "panel-top","command","corner-down-left","arrow-up","arrow-down","maximize-2","minimize-2",
  "copy","clipboard-list","clipboard-check","map-pin","navigation","compass","radio","antenna",
  "satellite","smartphone","monitor","laptop","hard-drive-download","hard-drive-upload",
  "database-zap","file-code-2","terminal","braces","bug","wrench","hammer","truck","gift",
  "trophy","medal","award","crown","gem","diamond","star","thumbs-up","thumbs-down","message-square",
  "messages-square","mail","phone","globe","earth","magnet","anchor","ship","plane","train-front",
  "car","fuel","droplets","wind","waves","mountain","tree-pine","leaf","flower-2","sprout"
];

const out = {};
const missing = [];
// 别名映射（lucide 新命名 → 旧文件名）
const ALIAS = {
  "bar-chart-3": "chart-column",
  "alert-triangle": "triangle-alert",
  "check-circle-2": "circle-check",
  "x-circle": "circle-x",
  "filter": "funnel",
  "unlock": "lock-open",
  "line-chart": "chart-line",
  "pie-chart": "chart-pie",
  "bar-chart-4": "chart-column",
  "area-chart": "chart-area",
  "scatter-chart": "chart-scatter",
  "help-circle": "circle-question-mark",
};
for (const name of WANT) {
  const real = ALIAS[name] || name;
  const fp = path.join(SRC, real + ".js");
  if (!fs.existsSync(fp)) { missing.push(name); continue; }
  let src = fs.readFileSync(fp, "utf8");
  // 提取 __iconNode 数组文本
  const m = src.match(/const __iconNode = (\[[\s\S]*?\]);\s*\nconst /);
  if (!m) { missing.push(name + "(parse)"); continue; }
  try {
    out[name] = JSON.parse(m[1]
      .replace(/'/g, '"')            // 单引号→双引号
      .replace(/([{,]\s*)([a-zA-Z_$][\w$]*)\s*:/g, '$1"$2":') // 键加引号
      .replace(/,(\s*[}\]])/g, '$1')  // 去尾逗号
    );
  } catch (e) {
    missing.push(name + "(json: " + e.message.slice(0, 40) + ")");
  }
}

const js = `/* 极光引擎 Aurora — 图标库（自动提取自项目依赖 lucide-react v0.536.0，ISC 协议）
   渲染函数 AURORA.renderIco(name) → SVG 字符串；<i data-ico="name"> 由外壳自动填充 */
window.AURORA_ICONS = ${JSON.stringify(out, null, 0)};
window.AURORA = window.AURORA || {};
AURORA.renderIco = function (name, cls, extra) {
  var node = AURORA_ICONS[name];
  if (!node) return '';
  var s = '<svg class="' + (cls || '') + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ' + (extra || '') + '>';
  node.forEach(function (part) {
    var el = part[0], a = part[1] || {};
    if (el === 'path') s += '<path d="' + a.d + '"/>';
    else if (el === 'circle') s += '<circle cx="' + a.cx + '" cy="' + a.cy + '" r="' + a.r + '"/>';
    else if (el === 'rect') s += '<rect x="' + a.x + '" y="' + a.y + '" width="' + a.width + '" height="' + a.height + '"' + (a.rx ? ' rx="' + a.rx + '"' : '') + '/>';
    else if (el === 'line') s += '<line x1="' + a.x1 + '" y1="' + a.y1 + '" x2="' + a.x2 + '" y2="' + a.y2 + '"/>';
    else if (el === 'polyline') s += '<polyline points="' + a.points + '"/>';
    else if (el === 'polygon') s += '<polygon points="' + a.points + '"/>';
    else if (el === 'ellipse') s += '<ellipse cx="' + a.cx + '" cy="' + a.cy + '" rx="' + a.rx + '" ry="' + a.ry + '"/>';
  });
  return s + '</svg>';
};
`;

fs.writeFileSync(OUT, js);
console.log("icons extracted:", Object.keys(out).length);
console.log("missing:", missing.length ? missing.join(", ") : "none");
console.log("output:", OUT, "(" + Math.round(js.length / 1024) + " KB)");
