"use client";

import { useEffect, useState } from "react";
import { Search, Bell, LogOut, RefreshCw } from "lucide-react";
import { hardNavigate } from "@/lib/app-nav";
import { isElectronRuntime } from "@/lib/auth-storage";
import { getWs } from "@/lib/ws";
import { useAuthStore } from "@/lib/stores/auth";
import { useUIStore } from "@/lib/stores/ui";
import { apiRequest } from "@/lib/api";
import type { UpdaterState } from "@/types/electron";

/** 报错角标轮询间隔（R5-2：魔法数字提为常量） */
const ALERT_POLL_MS = 30_000;

export function TopBar({ wsConnected }: { wsConnected: boolean }) {
  const [time, setTime] = useState(() =>
    new Date().toLocaleTimeString("zh-CN", { hour12: false })
  );
  const [loggingOut, setLoggingOut] = useState(false);
  const [appVersion, setAppVersion] = useState("");
  const [updBusy, setUpdBusy] = useState(false);
  const [updHint, setUpdHint] = useState("");
  const [alertCount, setAlertCount] = useState(0);
  const [notifOpen, setNotifOpen] = useState(false);
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  // R5-1：搜索框接命令面板（focus 即打开、输入即过滤）
  const openPalette = useUIStore((s) => s.openCommandPalette);
  const setPaletteQuery = useUIStore((s) => s.setPaletteQuery);

  useEffect(() => {
    const id = setInterval(
      () => setTime(new Date().toLocaleTimeString("zh-CN", { hour12: false })),
      1000
    );
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
    const id = setInterval(() => void load(), ALERT_POLL_MS);
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
    <header className="relative flex items-center justify-between h-12 px-4 border-b border-border bg-card/60 backdrop-blur-md flex-shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-muted/50 border border-border text-muted-foreground text-xs w-64 focus-within:border-cyan-400/60 focus-within:ring-3 focus-within:ring-cyan-400/15 transition-all">
          <Search className="w-3.5 h-3.5" />
          <input
            type="text"
            placeholder="搜索... (Ctrl+K)"
            aria-label="搜索页面或功能（打开命令面板）"
            onFocus={() => openPalette()}
            onChange={(e) => {
              openPalette();
              setPaletteQuery(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") e.currentTarget.blur();
            }}
            className="flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground/60"
          />
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-xs">
          {wsConnected ? (
            <span className="chip-capsule ws"><span className="w-1.5 h-1.5 rounded-full bg-profit shadow-[0_0_6px_rgba(52,211,153,0.8)]" />实时</span>
          ) : (
            <span className="chip-capsule"><span className="w-1.5 h-1.5 rounded-full bg-muted-foreground" />轮询中</span>
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
              className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-1 text-[11px] hover:border-cyan-400/40 hover:bg-cyan-400/10 hover:text-cyan-300 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-3 h-3 ${updBusy ? "animate-spin" : ""}`} />
              {updHint || "检查更新"}
            </button>
          </div>
        ) : null}

        {/* 通知中心（Aurora 设计稿组件：铃铛下拉面板） */}
        <div className="relative">
          <a
            href="/ops#ops-errors"
            aria-label="运维报错中心"
            title={alertCount > 0 ? `P0/P1 报错 ${alertCount}` : "通知中心"}
            className="text-muted-foreground hover:text-foreground transition-colors relative"
            onClick={(e) => {
              e.preventDefault();
              setNotifOpen((v) => !v);
            }}
          >
            <Bell className="w-4 h-4" />
            {alertCount > 0 ? (
              <span className="absolute -top-0.5 -right-0.5 min-w-[14px] h-3.5 px-0.5 rounded-full bg-loss text-[9px] leading-[14px] text-center text-white">
                {alertCount > 99 ? "99+" : alertCount}
              </span>
            ) : null}
          </a>
          {notifOpen && (
            <div className="absolute right-0 top-8 z-50 w-[360px] overflow-hidden rounded-xl border border-border bg-popover shadow-[0_20px_60px_rgba(0,0,0,0.5)]">
              <div className="flex items-center justify-between px-4 py-3 border-b border-border/60">
                <span className="text-[13px] font-bold">通知中心</span>
                <button
                  className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                  onClick={() => setNotifOpen(false)}
                >
                  关闭
                </button>
              </div>
              {alertCount > 0 ? (
                <a
                  href="/ops#ops-errors"
                  onClick={() => setNotifOpen(false)}
                  className="flex gap-3 px-4 py-3 border-b border-border/40 hover:bg-white/[0.04] transition-colors"
                >
                  <span className="w-7 h-7 rounded-lg bg-loss/15 text-loss flex items-center justify-center flex-shrink-0">
                    <Bell className="w-3.5 h-3.5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-xs font-semibold">运维报错 {alertCount} 条</span>
                    <span className="block text-[11px] text-muted-foreground leading-snug mt-0.5">
                      P0/P1 级错误待处理，点击前往报错中心
                    </span>
                  </span>
                </a>
              ) : (
                <div className="px-4 py-8 text-center text-xs text-muted-foreground">暂无新通知</div>
              )}
              {updHint && (
                <div className="flex gap-3 px-4 py-3 border-b border-border/40 hover:bg-white/[0.04] transition-colors">
                  <span className="w-7 h-7 rounded-lg bg-cyan-400/15 text-cyan-300 flex items-center justify-center flex-shrink-0">
                    <RefreshCw className="w-3.5 h-3.5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-xs font-semibold">桌面端更新</span>
                    <span className="block text-[11px] text-muted-foreground leading-snug mt-0.5">{updHint}</span>
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="text-xs font-mono text-muted-foreground tabular-nums">{time}</div>
        <div className="w-px h-5 bg-border" />
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-cyan-400/20 flex items-center justify-center">
            <span className="text-cyan-300 text-[10px] font-bold">{initial}</span>
          </div>
          <span className="text-xs text-muted-foreground hidden sm:block max-w-[100px] truncate">
            {user?.username || "—"}
          </span>
          {user?.tier && (
            <span className="hidden md:inline text-[10px] uppercase tracking-wide text-cyan-400/80">
              {user.tier}
            </span>
          )}
          <button
            type="button"
            onClick={() => void onLogout()}
            disabled={loggingOut}
            aria-label="退出登录"
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
