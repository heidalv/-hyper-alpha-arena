// frontend-next/electron/main.js
// Electron 主进程:加载 Next.js 产物(生产)或 dev server(开发)。
// 说明:故意写成 plain JS(Electron 主进程跑 Node,避免 ts-node/tsx 启动复杂度)。
const { app, BrowserWindow, ipcMain, safeStorage, dialog } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const { URL } = require("url");
const log = require("electron-log");

let win = null;
let staticServer = null;
let autoUpdaterRef = null;

/** @type {{ status: string, version?: string, percent?: number, error?: string, currentVersion: string }} */
const updaterState = {
  status: "idle",
  currentVersion: "0.0.0",
};

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".map": "application/json",
};

function tokenFilePath() {
  return path.join(app.getPath("userData"), "auth-tokens.bin");
}

function backendUrlFilePath() {
  return path.join(app.getPath("userData"), "backend-url.txt");
}

function readSavedBackendUrl() {
  try {
    const fp = backendUrlFilePath();
    if (!fs.existsSync(fp)) return "";
    return String(fs.readFileSync(fp, "utf8") || "").trim().replace(/\/$/, "");
  } catch (e) {
    log.warn("[config] read backend url:", e?.message || e);
    return "";
  }
}

function writeSavedBackendUrl(url) {
  const cleaned = String(url || "").trim().replace(/\/$/, "");
  if (!cleaned) return { ok: false, error: "empty url" };
  try {
    fs.writeFileSync(backendUrlFilePath(), cleaned, "utf8");
    return { ok: true, url: cleaned };
  } catch (e) {
    log.warn("[config] write backend url:", e?.message || e);
    return { ok: false, error: String(e?.message || e) };
  }
}

function feedUrlFromBackend(backendBase) {
  const base = String(backendBase || "").trim().replace(/\/$/, "");
  if (!base) return "http://127.0.0.1:8000/arena-updates/";
  return `${base}/arena-updates/`;
}

function encryptPayload(obj) {
  const raw = Buffer.from(JSON.stringify(obj), "utf8");
  if (safeStorage.isEncryptionAvailable()) {
    return safeStorage.encryptString(raw.toString("utf8"));
  }
  // 无系统加密时退化为明文文件(仅开发兜底,仍放在 userData)
  return raw;
}

function decryptPayload(buf) {
  if (!buf || !buf.length) return null;
  try {
    if (safeStorage.isEncryptionAvailable()) {
      const text = safeStorage.decryptString(buf);
      return JSON.parse(text);
    }
    return JSON.parse(buf.toString("utf8"));
  } catch (e) {
    log.warn("[auth] decrypt failed:", e?.message || e);
    return null;
  }
}

function pushUpdaterEvent(payload) {
  Object.assign(updaterState, payload || {});
  try {
    if (win && !win.isDestroyed()) {
      win.webContents.send("updater:event", { ...updaterState });
    }
  } catch (e) {
    log.warn("[updater] push event:", e?.message || e);
  }
}

function setupAuthIpc() {
  ipcMain.handle("auth:getTokens", () => {
    try {
      const fp = tokenFilePath();
      if (!fs.existsSync(fp)) return { accessToken: null, refreshToken: null };
      const buf = fs.readFileSync(fp);
      const data = decryptPayload(buf);
      return {
        accessToken: data?.accessToken || null,
        refreshToken: data?.refreshToken || null,
      };
    } catch (e) {
      log.warn("[auth] getTokens:", e?.message || e);
      return { accessToken: null, refreshToken: null };
    }
  });

  ipcMain.handle("auth:setTokens", (_evt, payload) => {
    try {
      const accessToken = payload?.accessToken || "";
      const refreshToken = payload?.refreshToken || "";
      const enc = encryptPayload({ accessToken, refreshToken });
      fs.writeFileSync(tokenFilePath(), enc);
      return { ok: true };
    } catch (e) {
      log.warn("[auth] setTokens:", e?.message || e);
      return { ok: false, error: String(e?.message || e) };
    }
  });

  ipcMain.handle("auth:clearTokens", () => {
    try {
      const fp = tokenFilePath();
      if (fs.existsSync(fp)) fs.unlinkSync(fp);
      return { ok: true };
    } catch (e) {
      log.warn("[auth] clearTokens:", e?.message || e);
      return { ok: false };
    }
  });

  ipcMain.handle("config:getBackendUrl", () => {
    return { url: readSavedBackendUrl() || "http://127.0.0.1:8000" };
  });

  ipcMain.handle("config:setBackendUrl", (_evt, payload) => {
    const res = writeSavedBackendUrl(payload?.url || payload);
    if (res.ok && autoUpdaterRef && app.isPackaged) {
      try {
        const feed = feedUrlFromBackend(res.url);
        autoUpdaterRef.setFeedURL({ provider: "generic", url: feed });
        log.info("[updater] feed url updated →", feed);
      } catch (e) {
        log.warn("[updater] setFeedURL after backend change:", e?.message || e);
      }
    }
    return res;
  });

  ipcMain.handle("updater:getStatus", () => ({ ...updaterState }));

  ipcMain.handle("updater:getVersion", () => ({
    version: app.getVersion(),
    isPackaged: app.isPackaged,
  }));

  ipcMain.handle("updater:check", async () => {
    if (!app.isPackaged) {
      pushUpdaterEvent({ status: "dev-skip", error: "开发模式不检查更新" });
      return { ok: false, reason: "dev" };
    }
    if (!autoUpdaterRef) {
      pushUpdaterEvent({ status: "error", error: "updater 未初始化" });
      return { ok: false, reason: "no-updater" };
    }
    try {
      const saved = readSavedBackendUrl();
      const feed = feedUrlFromBackend(saved);
      autoUpdaterRef.setFeedURL({ provider: "generic", url: feed });
      log.info("[updater] manual check feed=", feed);
      pushUpdaterEvent({ status: "checking", error: undefined });
      const result = await autoUpdaterRef.checkForUpdates();
      return { ok: true, updateInfo: result?.updateInfo || null };
    } catch (e) {
      const msg = String(e?.message || e);
      pushUpdaterEvent({ status: "error", error: msg });
      return { ok: false, error: msg };
    }
  });

  ipcMain.handle("updater:install", () => {
    if (!autoUpdaterRef) return { ok: false };
    try {
      // true = 强制退出并安装
      autoUpdaterRef.quitAndInstall(false, true);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e?.message || e) };
    }
  });
}

/** Next output:export + trailingSlash:false → /login 对应 login.html */
function resolveStaticFile(rootDir, pathname) {
  let rel = decodeURIComponent(pathname || "/");
  if (rel.includes("..")) return null;
  if (rel === "/") rel = "/index.html";
  const abs = path.join(rootDir, rel);
  if (fs.existsSync(abs) && fs.statSync(abs).isFile()) return abs;
  if (!path.extname(rel)) {
    const asHtml = path.join(rootDir, `${rel}.html`);
    if (fs.existsSync(asHtml)) return asHtml;
    const asIndex = path.join(rootDir, rel, "index.html");
    if (fs.existsSync(asIndex)) return asIndex;
  }
  const notFound = path.join(rootDir, "404.html");
  return fs.existsSync(notFound) ? notFound : null;
}

/**
 * 生产环境用本地 http 提供 out/，避免 file:// 下 Next 客户端路由 /dashboard 失效。
 * @returns {Promise<string>} base URL e.g. http://127.0.0.1:54321
 */
function startStaticServer(rootDir) {
  return new Promise((resolve, reject) => {
    if (!fs.existsSync(rootDir)) {
      reject(new Error(`static root missing: ${rootDir}`));
      return;
    }
    const server = http.createServer((req, res) => {
      try {
        const u = new URL(req.url || "/", "http://127.0.0.1");
        const filePath = resolveStaticFile(rootDir, u.pathname);
        if (!filePath) {
          res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
          res.end("Not Found");
          return;
        }
        const ext = path.extname(filePath).toLowerCase();
        res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
        fs.createReadStream(filePath).pipe(res);
      } catch (e) {
        log.warn("[static]", e?.message || e);
        res.writeHead(500);
        res.end("Internal Error");
      }
    });
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      staticServer = server;
      const { port } = server.address();
      resolve(`http://127.0.0.1:${port}`);
    });
  });
}

function outDirPath() {
  return path.join(__dirname, "..", "out");
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: "Heidalv Alpha Arena",
    backgroundColor: "#0A0E14",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // safeStorage IPC 需要
    },
  });

  const isDev = !app.isPackaged;
  if (isDev) {
    // 开发:连 next dev server (port 5273)
    win.loadURL("http://127.0.0.1:5273/login");
    // 需要调试时取消下一行注释
    // win.webContents.openDevTools({ mode: "detach" });
  } else {
    try {
      const base = await startStaticServer(outDirPath());
      win.loadURL(`${base}/login`);
      log.info("[main] static server", base);
    } catch (e) {
      log.error("[main] static server failed:", e?.message || e);
      const fallback = [
        path.join(outDirPath(), "login.html"),
        path.join(outDirPath(), "index.html"),
      ].find((p) => fs.existsSync(p));
      if (fallback) win.loadFile(fallback);
    }
  }

  win.on("closed", () => {
    win = null;
  });
}

function setupAutoUpdater() {
  updaterState.currentVersion = app.getVersion();
  if (!app.isPackaged) {
    pushUpdaterEvent({ status: "dev-skip", currentVersion: app.getVersion() });
    return;
  }
  try {
    const { autoUpdater } = require("electron-updater");
    autoUpdaterRef = autoUpdater;
    autoUpdater.logger = log;
    autoUpdater.logger.transports.file.level = "info";
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;

    const saved = readSavedBackendUrl();
    const feed = feedUrlFromBackend(saved);
    autoUpdater.setFeedURL({ provider: "generic", url: feed });
    log.info("[updater] feed=", feed);

    autoUpdater.on("checking-for-update", () => {
      pushUpdaterEvent({ status: "checking", error: undefined });
    });
    autoUpdater.on("update-available", (info) => {
      pushUpdaterEvent({
        status: "available",
        version: info?.version,
        error: undefined,
      });
    });
    autoUpdater.on("update-not-available", () => {
      pushUpdaterEvent({ status: "not-available", error: undefined });
    });
    autoUpdater.on("download-progress", (p) => {
      pushUpdaterEvent({
        status: "downloading",
        percent: Math.round(Number(p?.percent || 0)),
      });
    });
    autoUpdater.on("update-downloaded", async (info) => {
      pushUpdaterEvent({
        status: "downloaded",
        version: info?.version,
        percent: 100,
        error: undefined,
      });
      log.info(`[updater] Update downloaded v${info?.version}; prompt install`);
      try {
        const res = await dialog.showMessageBox(win || undefined, {
          type: "info",
          title: "发现新版本",
          message: `新版本 v${info?.version || "?"} 已下载完成`,
          detail: "点击「立即安装」将关闭程序并完成更新。",
          buttons: ["立即安装", "稍后"],
          defaultId: 0,
          cancelId: 1,
        });
        if (res.response === 0) {
          autoUpdater.quitAndInstall(false, true);
        }
      } catch (e) {
        log.warn("[updater] install dialog:", e?.message || e);
      }
    });
    autoUpdater.on("error", (e) => {
      const msg = String(e?.message || e);
      pushUpdaterEvent({ status: "error", error: msg });
      log.warn("[updater] error:", msg);
    });

    // 启动稍后再查，避免抢登录首屏
    setTimeout(() => {
      autoUpdater.checkForUpdates().catch((e) => {
        log.warn("[updater] initial check:", e?.message || e);
      });
    }, 5000);
    log.info("[updater] scheduled initial check");
  } catch (e) {
    log.warn("[updater] electron-updater not available:", e?.message || e);
    pushUpdaterEvent({ status: "error", error: String(e?.message || e) });
  }
}

app.whenReady().then(async () => {
  setupAuthIpc();
  await createWindow();
  setupAutoUpdater();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
});

app.on("window-all-closed", () => {
  if (staticServer) {
    try {
      staticServer.close();
    } catch (_) {
      /* ignore */
    }
    staticServer = null;
  }
  if (process.platform !== "darwin") app.quit();
});
