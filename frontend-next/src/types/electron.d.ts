/** Electron preload 暴露给渲染进程的类型 */
export interface ElectronAuthAPI {
  getTokens: () => Promise<{
    accessToken: string | null;
    refreshToken: string | null;
  }>;
  setTokens: (accessToken: string, refreshToken: string) => Promise<{ ok: boolean; error?: string }>;
  clearTokens: () => Promise<{ ok: boolean }>;
}

export interface ElectronAPI {
  platform: string;
  isElectron: boolean;
  isPackaged: boolean;
  auth: ElectronAuthAPI;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export {};
