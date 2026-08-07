// frontend-next/electron/main.js
// Electron 主进程:加载 Next.js 产物(生产)或 dev server(开发)。
// 说明:故意写成 plain JS(Electron 主进程跑 Node,避免 ts-node/tsx 启动复杂度)。
const { app, BrowserWindow, ipcMain, safeStorage } = require("electron");
const path = require("path");
const fs = require("fs");
const http = require("http");
const { URL } = require("url");
const log = require("electron-log");

let win = null;
let staticServer = null;

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
  if (!app.isPackaged) return;
  try {
    const { autoUpdater } = require("electron-updater");
    autoUpdater.logger = log;
    autoUpdater.logger.transports.file.level = "info";
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;

    autoUpdater.on("update-downloaded", (info) => {
      log.info(`[updater] Update downloaded v${info.version}; will install on next restart`);
    });
    autoUpdater.on("error", (e) => {
      log.warn("[updater] error:", e?.message || e);
    });

    autoUpdater.checkForUpdatesAndNotify();
    log.info("[updater] checkForUpdatesAndNotify scheduled");
  } catch (e) {
    log.warn("[updater] electron-updater not available:", e?.message || e);
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
