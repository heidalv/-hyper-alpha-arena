// 构建 Aurora 全站预览：合并 aurora.css + shell.html + pages/*.html → index.html
// 用法: node build.js
const fs = require("fs");
const path = require("path");

const dir = __dirname;
const css = fs.readFileSync(path.join(dir, "aurora.css"), "utf8");
const icons = fs.readFileSync(path.join(dir, "icons.js"), "utf8");
let shell = fs.readFileSync(path.join(dir, "shell.html"), "utf8");

const pagesDir = path.join(dir, "pages");
const files = fs
  .readdirSync(pagesDir)
  .filter((f) => f.endsWith(".html"))
  .sort();

let pagesHtml = "";
for (const f of files) {
  const frag = fs.readFileSync(path.join(pagesDir, f), "utf8").trim();
  pagesHtml += "\n" + frag + "\n";
}

// 校验片段：不允许 html/head/body 标签；必须含 <section
for (const f of files) {
  const frag = fs.readFileSync(path.join(pagesDir, f), "utf8");
  if (/<html|<head|<body/i.test(frag)) {
    console.error("ERROR: " + f + " 包含 html/head/body 标签");
    process.exit(1);
  }
  if (!/<section/i.test(frag)) {
    console.error("ERROR: " + f + " 缺少 <section");
    process.exit(1);
  }
}

shell = shell.replace("{{ICONS}}", icons).replace("{{CSS}}", css).replace("{{PAGES}}", pagesHtml);
const out = path.join(dir, "index.html");
fs.writeFileSync(out, shell);

// 标签平衡校验（忽略 style/script 内容与 void/self-closed）
const src = shell
  .replace(/<style>[\s\S]*?<\/style>/g, "")
  .replace(/<script>[\s\S]*?<\/script>/g, "")
  .replace(/<!--[\s\S]*?-->/g, "")
  .replace(/<[a-zA-Z][^>]*\/\s*>/g, "");
const VOID = new Set(["meta","link","img","input","br","hr","source","wbr","area","base","col","embed","track","param","!doctype"]);
const count = {};
for (const m of src.matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)(?=[\s>])/g)) {
  const t = m[1].toLowerCase();
  if (!VOID.has(t)) count[t] = (count[t] || 0) + 1;
}
for (const m of src.matchAll(/<\/([a-zA-Z][a-zA-Z0-9-]*)>/g)) {
  const t = m[1].toLowerCase();
  count[t] = (count[t] || 0) - 1;
}
const bad = Object.entries(count).filter(([, n]) => n !== 0).map(([t, n]) => t + ":" + (n > 0 ? "+" : "") + n);
console.log("pages merged: " + files.length);
console.log("output: " + out + " (" + Math.round(shell.length / 1024) + " KB)");
console.log(bad.length ? "UNBALANCED: " + bad.join(" ") : "TAGS BALANCED OK");
