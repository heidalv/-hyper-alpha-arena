"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { TickerBar } from "./TickerBar";
import { StatusBar } from "./StatusBar";
import { CommandPalette } from "./CommandPalette";
import { AuthGate } from "@/components/auth/AuthGate";
import { DesktopUpdateBanner } from "@/components/desktop/DesktopUpdateBanner";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useMarketStore } from "@/lib/stores/market";
import { useAuthStore } from "@/lib/stores/auth";

function ShellBody({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const wsConnected = useMarketStore((s) => s.wsConnected);
  const user = useAuthStore((s) => s.user);

  // 仅登录后连接 WebSocket
  useWebSocket(!!user);

  return (
    <div className="relative flex h-screen overflow-hidden">
      {/* 极光背景层（Aurora 签名元素：光斑 + 光带 + 星点 + 扫描带） */}
      <div className="aurora-bg" aria-hidden="true">
        <div className="aurora-blob aurora-blob-cyan" />
        <div className="aurora-blob aurora-blob-violet" />
        <div className="aurora-blob aurora-blob-emerald" />
        <div className="aurora-band" />
        <div className="aurora-stars" />
      </div>
      <div className="aurora-grid" aria-hidden="true" />
      <div className="aurora-scanline" aria-hidden="true" />
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
      <div className="relative z-10 flex flex-1 flex-col overflow-hidden">
        <DesktopUpdateBanner />
        <TopBar wsConnected={wsConnected} />
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <TickerBar />
          <main className="flex-1 overflow-auto">{children}</main>
        </div>
        <StatusBar />
        <CommandPalette />
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login" || pathname?.startsWith("/login/");

  return (
    <AuthGate>
      {isLogin ? (
        <>
          {/* 登录页也挂极光背景（全屏签名元素） */}
          <div className="aurora-bg" aria-hidden="true">
            <div className="aurora-blob aurora-blob-cyan" />
            <div className="aurora-blob aurora-blob-violet" />
            <div className="aurora-blob aurora-blob-emerald" />
            <div className="aurora-band" />
            <div className="aurora-stars" />
          </div>
          <div className="aurora-grid" aria-hidden="true" />
          <div className="aurora-scanline" aria-hidden="true" />
          <div className="relative z-10">
            <DesktopUpdateBanner />
            {children}
          </div>
        </>
      ) : (
        <ShellBody>{children}</ShellBody>
      )}
    </AuthGate>
  );
}
