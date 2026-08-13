/**
 * UI 状态 Store — 侧边栏/命令面板等
 * R5-1：命令面板由本 store 驱动（Ctrl+K 快捷键 / TopBar 搜索框均走这里）。
 */
import { create } from "zustand";

interface UIState {
  sidebarCollapsed: boolean;
  commandPaletteOpen: boolean;
  paletteQuery: string;

  toggleSidebar: () => void;
  openCommandPalette: (query?: string) => void;
  closeCommandPalette: () => void;
  setPaletteQuery: (query: string) => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  commandPaletteOpen: false,
  paletteQuery: "",

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  openCommandPalette: (query = "") => set({ commandPaletteOpen: true, paletteQuery: query }),
  closeCommandPalette: () => set({ commandPaletteOpen: false }),
  setPaletteQuery: (query) => set({ paletteQuery: query }),
}));
