"use client";

import { useEffect, useState } from "react";
import { Search, Bell, Wifi, WifiOff, LogOut, RefreshCw } from "lucide-react";
import { hardNavigate } from "@/lib/app-nav";
import { isElectronRuntime } from "@/lib/auth-storage";
import { getWs } from "@/lib/ws";
import { useAuthStore } from "@/lib/stores/auth";
import { apiRequest } from "@/lib/api";
import type { UpdaterState } from "@/types/electron";

export function TopBar({ wsConnected }: { wsConnected: boolean }) {
  const [time, setTime] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const [appVersion, setAppVersion] = useState("");
  const [updBusy, setUpdBusy] = useState(false);
  const [updHint, setUpdHint] = useState("");
  const [alertCount, setAlertCount] = useState(0);
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  useEffect(() => {
    const update = () => {
      setTime(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    };
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await apiRequest<{ counts?: { P0?: number; P1?: number } }>(
          "/ops/errors?limit=1",
          { timeout: 8000 },
        );
        if (cancelled) return;
        const n = Number(data?.counts?.P0 || 0) + Number(data?.counts?.P1 || 0);
        setAlertCount(n);
      } catch {
        if (!cancelled) setAlertCount(0);
      }
    };
    void load();
    const id = setInterval(() => void load(), 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!isElectronRuntime() || !window.electronAPI?.updater) return;
    void window.electronAPI.updater.getVersion().then((r) => {
      if (r?.version) setAppVersion(r.version);
    });
    const off = window.electronAPI.updater.onEvent((s: UpdaterState) => {
      if (s.status === "checking") setUpdHint("检查中…");
      else if (s.status === "downloading") setUpdHint(`下载 ${s.percent ?? 0}%`);
      else if (s.status === "downloaded") setUpdHint(`v${s.version} 已就绪`);
      else if (s.status === "not-available") setUpdHint("已是最新");
      else if (s.status === "error") setUpdHint("更新失败");
      else if (s.status === "available") setUpdHint(`发现 v${s.version}`);
      setUpdBusy(s.status === "checking" || s.status === "downloading");
    });
    return () => off();
  }, []);

  useEffect(() => {
    if (!isElectronRuntime()) return;
    return getWs().subscribe((data) => {
      if (data?.type === "desktop_update" && data?.version) {
        setUpdHint(`新版本 v${data.version} 可更新`);
        setUpdBusy(false);
      }
    });
  }, []);

  const initial = (user?.username || "U").slice(0, 1).toUpperCase();

  async function onLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      /* 本地已清，仍去登录页 */
    }
    hardNavigate("/login");
  }

  async function onCheckUpdate() {
    if (!window.electronAPI?.updater || updBusy) return;
    setUpdBusy(true);
    setUpdHint("检查中…");
    try {
      const r = await window.electronAPI.updater.check();
      if (!r.ok && r.reason === "dev") setUpdHint("开发模式");
      else if (!r.ok) setUpdHint(r.error ? "更新失败" : "无结果");
    } catch {
      setUpdHint("更新失败");
      setUpdBusy(false);
    }
  }

  return (
    <header className="flex items-center justify-between h-12 px-4 border-b border-border bg-card flex-shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 px-3 py-1 rounded bg-muted/50 text-muted-foreground text-xs w-64">
          <Search className="w-3.5 h-3.5" />
          <span className="text-muted-foreground/60">搜索... (Ctrl+K)</span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-xs">
          {wsConnected ? (
            <>
              <Wifi className="w-3.5 h-3.5 text-profit" />
              <span className="text-profit">实时</span>
            </>
          ) : (
            <>
              <WifiOff className="w-3.5 h-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">轮询中</span>
            </>
          )}
        </div>

        {isElectronRuntime() ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {appVersion ? <span className="font-mono tabular-nums">v{appVersion}</span> : null}
            <button
              type="button"
              onClick={() => void onCheckUpdate()}
              disabled={updBusy}
              title="检查桌面端更新"
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] hover:border-emerald-500/40 hover:bg-emerald-500/10 hover:text-emerald-300 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-3 h-3 ${updBusy ? "animate-spin" : ""}`} />
              {updHint || "检查更新"}
            </button>
          </div>
        ) : null}

        <a
          href="/ops#ops-errors"
          title={alertCount > 0 ? `P0/P1 报错 ${alertCount}` : "运维报错中心"}
          className="text-muted-foreground hover:text-foreground transition-colors relative"
        >
          <Bell className="w-4 h-4" />
          {alertCount > 0 ? (
            <span className="absolute -top-0.5 -right-0.5 min-w-[14px] h-3.5 px-0.5 rounded-full bg-loss text-[9px] leading-[14px] text-center text-white">
              {alertCount > 99 ? "99+" : alertCount}
            </span>
          ) : null}
        </a>

        <div className="text-xs font-mono text-muted-foreground tabular-nums">{time}</div>
        <div className="w-px h-5 bg-border" />
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-emerald-500/20 flex items-center justify-center">
            <span className="text-emerald-400 text-[10px] font-bold">{initial}</span>
          </div>
          <span className="text-xs text-muted-foreground hidden sm:block max-w-[100px] truncate">
            {user?.username || "—"}
          </span>
          {user?.tier && (
            <span className="hidden md:inline text-[10px] uppercase tracking-wide text-emerald-500/80">
              {user.tier}
            </span>
          )}
          <button
            type="button"
            onClick={() => void onLogout()}
            disabled={loggingOut}
            title="退出登录"
            className="ml-1 inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50 transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            {loggingOut ? "退出中…" : "退出"}
          </button>
        </div>
      </div>
    </header>
  );
}
