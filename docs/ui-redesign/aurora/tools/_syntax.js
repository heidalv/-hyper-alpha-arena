const fs = require("fs");
const src = fs.readFileSync("D:/001Alpha/Hyper-Alpha-Arena/docs/ui-redesign/aurora/index.html", "utf8");
const scripts = [...src.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
console.log("script 块:", scripts.length);
scripts.forEach((s, i) => {
  try {
    new Function(s);
    console.log("script#" + i + ": 语法 OK (" + Math.round(s.length / 1024) + " KB)");
  } catch (e) {
    console.log("script#" + i + ": 语法错误 → " + e.message);
  }
});
