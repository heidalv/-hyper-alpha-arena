/** Electron preload 暴露给渲染进程的类型 */
export interface ElectronAuthAPI {
  getTokens: () => Promise<{
    accessToken: string | null;
    refreshToken: string | null;
  }>;
  setTokens: (accessToken: string, refreshToken: string) => Promise<{ ok: boolean; error?: string }>;
  clearTokens: () => Promise<{ ok: boolean }>;
}

export interface ElectronConfigAPI {
  getBackendUrl: () => Promise<{ url: string }>;
  setBackendUrl: (url: string) => Promise<{ ok: boolean; url?: string; error?: string }>;
}

export type UpdaterStatus =
  | "idle"
  | "checking"
  | "available"
  | "not-available"
  | "downloading"
  | "downloaded"
  | "error"
  | "dev-skip";

export interface UpdaterState {
  status: UpdaterStatus | string;
  version?: string;
  percent?: number;
  error?: string;
  currentVersion: string;
}

export interface ElectronUpdaterAPI {
  getVersion: () => Promise<{ version: string; isPackaged: boolean }>;
  getStatus: () => Promise<UpdaterState>;
  check: () => Promise<{ ok: boolean; reason?: string; error?: string; updateInfo?: unknown }>;
  install: () => Promise<{ ok: boolean; error?: string }>;
  onEvent: (cb: (state: UpdaterState) => void) => () => void;
}

export interface ElectronAPI {
  platform: string;
  isElectron: boolean;
  isPackaged: boolean;
  auth: ElectronAuthAPI;
  config?: ElectronConfigAPI;
  updater?: ElectronUpdaterAPI;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

export {};
