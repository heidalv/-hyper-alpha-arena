const fs = require("fs");
const path = require("path");
const dir = "D:/001Alpha/Hyper-Alpha-Arena/docs/ui-redesign/aurora/pages";
const VOID = new Set(["meta","link","img","input","br","hr","source","wbr","area","base","col","embed","track","param"]);
let allOk = true;
for (const f of fs.readdirSync(dir).filter(x => x.endsWith(".html")).sort()) {
  let src = fs.readFileSync(path.join(dir, f), "utf8");
  src = src.replace(/<!--[\s\S]*?-->/g, "").replace(/<[a-zA-Z][^>]*\/\s*>/g, "");
  const count = {};
  for (const m of src.matchAll(/<([a-zA-Z][a-zA-Z0-9-]*)(?=[\s>])/g)) { const t = m[1].toLowerCase(); if (!VOID.has(t)) count[t] = (count[t]||0)+1; }
  for (const m of src.matchAll(/<\/([a-zA-Z][a-zA-Z0-9-]*)>/g)) { const t = m[1].toLowerCase(); count[t] = (count[t]||0)-1; }
  const bad = Object.entries(count).filter(([,n]) => n !== 0).map(([t,n]) => t+":"+n);
  if (bad.length) allOk = false;
  console.log(f + ": " + (bad.length ? "UNBALANCED " + bad.join(" ") : "OK"));
}
console.log(allOk ? "ALL BALANCED" : "HAS ISSUES");
