"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, Bell, Wifi, WifiOff, LogOut } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth";

export function TopBar({ wsConnected }: { wsConnected: boolean }) {
  const [time, setTime] = useState("");
  const router = useRouter();
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

  const initial = (user?.username || "U").slice(0, 1).toUpperCase();

  async function onLogout() {
    await logout();
    router.replace("/login");
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

        <button className="text-muted-foreground hover:text-foreground transition-colors relative">
          <Bell className="w-4 h-4" />
          <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-loss" />
        </button>

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
            onClick={onLogout}
            title="退出登录"
            className="ml-1 text-muted-foreground hover:text-foreground transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </header>
  );
}
