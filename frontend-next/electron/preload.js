// frontend-next/electron/preload.js
// 渲染进程桥接:平台信息 + JWT safeStorage + 自动更新
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  platform: process.platform,
  isElectron: true,
  isPackaged: process.defaultApp === false,
  auth: {
    getTokens: () => ipcRenderer.invoke("auth:getTokens"),
    setTokens: (accessToken, refreshToken) =>
      ipcRenderer.invoke("auth:setTokens", { accessToken, refreshToken }),
    clearTokens: () => ipcRenderer.invoke("auth:clearTokens"),
  },
  config: {
    getBackendUrl: () => ipcRenderer.invoke("config:getBackendUrl"),
    setBackendUrl: (url) => ipcRenderer.invoke("config:setBackendUrl", { url }),
  },
  updater: {
    getVersion: () => ipcRenderer.invoke("updater:getVersion"),
    getStatus: () => ipcRenderer.invoke("updater:getStatus"),
    check: () => ipcRenderer.invoke("updater:check"),
    install: () => ipcRenderer.invoke("updater:install"),
    onEvent: (cb) => {
      const handler = (_evt, payload) => {
        try {
          cb(payload);
        } catch (_) {
          /* ignore */
        }
      };
      ipcRenderer.on("updater:event", handler);
      return () => ipcRenderer.removeListener("updater:event", handler);
    },
  },
});
