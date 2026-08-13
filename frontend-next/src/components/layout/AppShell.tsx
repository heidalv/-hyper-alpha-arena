"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
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
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <DesktopUpdateBanner />
        <TopBar wsConnected={wsConnected} />
        <main className="flex-1 overflow-auto">{children}</main>
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
          <DesktopUpdateBanner />
          {children}
        </>
      ) : (
        <ShellBody>{children}</ShellBody>
      )}
    </AuthGate>
  );
}
