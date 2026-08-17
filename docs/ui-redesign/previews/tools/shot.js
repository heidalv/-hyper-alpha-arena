// 用 Electron(Chromium) 给设计预览 HTML 截图 — 用法:
//   electron shot.js manifest.json
// manifest.json: [ { "html": "绝对路径或相对本文件路径", "out": "输出png", "waitMs": 1400 }, ... ]
const { app, BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");

const LOG = path.join(__dirname, "shot.log");
function log(msg) {
  try { fs.appendFileSync(LOG, `${new Date().toISOString()} ${msg}\n`); } catch {}
}

log("start argv=" + JSON.stringify(process.argv));
const manifestPath = process.argv[2];
if (!manifestPath) {
  log("ERROR: no manifest");
  process.exit(1);
}
const jobs = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
log("jobs=" + jobs.length);

app.whenReady().then(async () => {
  log("app ready");
  for (const job of jobs) {
    log("job: " + job.html);
    const win = new BrowserWindow({
      width: job.width || 1440,
      height: job.height || 900,
      show: false,
      webPreferences: { offscreen: true, backgroundThrottling: false },
    });
    log("window created");
    try {
      await win.loadFile(job.html);
      log("loaded");
      await new Promise((r) => setTimeout(r, job.waitMs || 1600));
      log("waited, capturing");
      const image = await win.webContents.capturePage();
      log("captured");
      const out = path.resolve(path.dirname(manifestPath), job.out);
      fs.mkdirSync(path.dirname(out), { recursive: true });
      fs.writeFileSync(out, image.toPNG());
      log("OK: " + job.out);
    } catch (e) {
      log("FAIL: " + job.html + " :: " + (e && e.message));
    }
    win.destroy();
  }
  log("done, quitting");
  app.quit();
});
