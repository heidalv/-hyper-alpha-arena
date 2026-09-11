/**
 * 统一确认框（替代原生 window.confirm）
 *
 * 背景：全站 23 处 `confirm()` / `window.confirm()`——阻塞、样式不可控、
 * 在 Electron 里是系统模态框，与 Aurora 深色玻璃语言完全脱节；
 * 破坏性操作也无法要求二次输入确认词。
 *
 * 用法：
 *   import { confirmDialog } from "@/lib/confirm";
 *   if (!(await confirmDialog({ title: "确认删除此账户？", tone: "danger" }))) return;
 *
 * 渲染在 <ConfirmDialog />（AppShell 挂载）。同一时刻只保留一个请求，
 * 新请求到达时旧请求以 false 结算，避免 Promise 悬挂。
 */
import { create } from "zustand";

export type ConfirmTone = "danger" | "warning" | "primary";

export interface ConfirmOptions {
  title: string;
  /** 补充说明（风险、后果） */
  description?: string;
  confirmText?: string;
  cancelText?: string;
  tone?: ConfirmTone;
  /** 要求用户原样输入该文字才能确认（实盘下单等高风险操作） */
  requireText?: string;
}

export interface ConfirmRequest extends ConfirmOptions {
  id: number;
}

interface ConfirmState {
  current: ConfirmRequest | null;
  /** 内部使用：由 confirmDialog 调用 */
  open: (req: ConfirmRequest, prev: ((ok: boolean) => void) | null) => void;
  settle: (ok: boolean) => void;
}

let seq = 0;
let pendingResolve: ((ok: boolean) => void) | null = null;

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  current: null,
  open: (req, prev) => {
    if (prev) prev(false);
    set({ current: req });
  },
  settle: (ok) => {
    const cur = get().current;
    set({ current: null });
    if (pendingResolve) {
      const r = pendingResolve;
      pendingResolve = null;
      r(ok);
    }
    void cur;
  },
}));

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const req: ConfirmRequest = {
      id: ++seq,
      confirmText: "确认",
      cancelText: "取消",
      tone: "primary",
      ...opts,
    };
    // 上一笔未结算的请求以「取消」结算，保证不悬挂
    const prev = pendingResolve;
    pendingResolve = resolve;
    useConfirmStore.getState().open(req, prev);
  });
}
