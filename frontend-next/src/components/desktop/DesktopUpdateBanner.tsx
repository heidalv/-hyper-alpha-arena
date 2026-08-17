"use client";

import { useEffect, useState } from "react";
import { Download, RefreshCw, X } from "lucide-react";
import { isElectronRuntime } from "@/lib/auth-storage";
import type { UpdaterState } from "@/types/electron";

/**
 * Electron 专用：有新版本下载完时显示顶部横幅，可立即安装重启。
 */
export function DesktopUpdateBanner() {
  const [state, setState] = useState<UpdaterState | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!isElectronRuntime() || !window.electronAPI?.updater) return;
    let off: (() => void) | undefined;
    void window.electronAPI.updater.getStatus().then(setState);
    off = window.electronAPI.updater.onEvent((s) => {
      setState(s);
      if (s.status === "downloaded" || s.status === "available") {
        setDismissed(false);
      }
    });
    return () => {
      off?.();
    };
  }, []);

  if (!isElectronRuntime() || !state || dismissed) return null;
  if (state.status !== "downloaded" && state.status !== "downloading" && state.status !== "available") {
    return null;
  }

  const installing = state.status === "downloaded";
  const pct = state.percent ?? 0;

  return (
    <div className="flex items-center justify-between gap-3 border-b border-profit/30 bg-profit/10 px-4 py-2 text-xs text-profit">
      <div className="flex min-w-0 items-center gap-2">
        <Download className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">
          {state.status === "downloading"
            ? `正在下载新版本 v${state.version || "…"}（${pct}%）`
            : state.status === "available"
              ? `发现新版本 v${state.version || "…"}，准备下载…`
              : `新版本 v${state.version || "…"} 已就绪，可立即安装`}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {installing ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-md bg-profit px-2.5 py-1 text-[11px] font-medium text-white hover:bg-profit/80"
            onClick={() => void window.electronAPI?.updater?.install()}
          >
            <RefreshCw className="h-3 w-3" />
            立即安装并重启
          </button>
        ) : null}
        <button
          type="button"
          className="rounded p-1 text-profit/70 hover:bg-profit/80/20 hover:text-white"
          title="稍后"
          onClick={() => setDismissed(true)}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
