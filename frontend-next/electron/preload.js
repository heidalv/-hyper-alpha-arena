// frontend-next/electron/preload.js
// 渲染进程桥接:平台信息 + JWT safeStorage 存取
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
});
