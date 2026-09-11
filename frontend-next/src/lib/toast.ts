/**
 * 轻量 Toast（Aurora 设计系统 §二级组件库 · 通知）
 *
 * 背景：设计稿定义了 Toast，但实现里从未落地，全站 32 处用浏览器原生
 * `alert()` 报错——阻塞、样式不可控、读屏体验差，且与深色玻璃语言完全脱节。
 *
 * 用法：
 *   import { toast } from "@/lib/toast";
 *   toast.error("保存失败：" + msg);
 *   toast.success("已保存");
 *   toast.info("正在重新扫描…");
 *
 * 渲染在 <Toaster />（AppShell 挂载），容器带 aria-live="polite"，
 * 因此错误提示对读屏用户同样可达（此前全站 aria-live 为 0 处）。
 */
import { create } from "zustand";

export type ToastTone = "success" | "error" | "info" | "warning";

export interface ToastItem {
  id: number;
  tone: ToastTone;
  message: string;
  /** 毫秒；0 表示不自动消失 */
  duration: number;
}

interface ToastState {
  items: ToastItem[];
  push: (tone: ToastTone, message: string, duration?: number) => number;
  dismiss: (id: number) => void;
  clear: () => void;
}

const DEFAULT_DURATION: Record<ToastTone, number> = {
  success: 3_000,
  info: 4_000,
  warning: 6_000,
  error: 8_000, // 错误多留一会儿，交易场景不宜转瞬即逝
};

let seq = 0;

export const useToastStore = create<ToastState>((set, get) => ({
  items: [],
  push: (tone, message, duration) => {
    const id = ++seq;
    const item: ToastItem = {
      id,
      tone,
      message: String(message ?? ""),
      duration: duration ?? DEFAULT_DURATION[tone],
    };
    // 最多同时 4 条，超出丢弃最旧的，避免刷屏遮挡盘面
    set((s) => ({ items: [...s.items.slice(-3), item] }));
    if (item.duration > 0) {
      setTimeout(() => get().dismiss(id), item.duration);
    }
    return id;
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
  clear: () => set({ items: [] }),
}));

export const toast = {
  success: (message: string, duration?: number) => useToastStore.getState().push("success", message, duration),
  error: (message: string, duration?: number) => useToastStore.getState().push("error", message, duration),
  info: (message: string, duration?: number) => useToastStore.getState().push("info", message, duration),
  warning: (message: string, duration?: number) => useToastStore.getState().push("warning", message, duration),
  dismiss: (id: number) => useToastStore.getState().dismiss(id),
  clear: () => useToastStore.getState().clear(),
};
