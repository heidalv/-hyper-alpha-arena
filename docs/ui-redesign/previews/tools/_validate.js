const fs = require("fs");
const path = require("path");
const dir = "D:/001Alpha/Hyper-Alpha-Arena/docs/ui-redesign/previews";
const VOID = new Set(["meta","link","img","input","br","hr","source","wbr","area","base","col","embed","track","param","!doctype"]);
for (const f of ["01-obsidian-terminal.html","02-aurora-engine.html","03-dawn-research.html","04-zen-mono.html"]) {
  let src = fs.readFileSync(path.join(dir, f), "utf8");
  src = src.replace(/<!--[\s\S]*?-->/g, "");            // strip comments
  src = src.replace(/<[a-zA-Z][^>]*\/\s*>/g, "");        // strip self-closed tags
  const opens = (src.match(/<([a-zA-Z][a-zA-Z0-9-]*)(?=[\s>])/g) || []).map(t => t.slice(1).toLowerCase())
    .filter(t => !VOID.has(t));
  const closes = (src.match(/<\/([a-zA-Z][a-zA-Z0-9-]*)>/g) || []).map(t => t.slice(2, -1).toLowerCase());
  const stack = [];
  let err = null;
  for (const t of opens) { if (["html","head","body","div","span","table","ul","style"].includes(t)) stack.push(t); }
  // simple balance check per tag type
  const count = {};
  for (const t of opens) count[t] = (count[t] || 0) + 1;
  for (const t of closes) count[t] = (count[t] || 0) - 1;
  const bad = Object.entries(count).filter(([,n]) => n !== 0).map(([t,n]) => `${t}:${n>0?"+":"-"}${Math.abs(n)}`);
  console.log(`${f}: ${bad.length ? "UNBALANCED " + bad.join(" ") : "TAGS BALANCED OK"}`);
}
