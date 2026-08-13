"use client";

import { useEffect, useLayoutEffect } from "react";
import { usePathname } from "next/navigation";
import { Loader2 } from "lucide-react";
import { hardNavigate } from "@/lib/app-nav";
import { useAuthStore } from "@/lib/stores/auth";

function isLoginPath(pathname: string | null | undefined): boolean {
  return pathname === "/login" || !!pathname?.startsWith("/login/");
}

/**
 * 路由守卫：
 * - 有本地/Electron 会话时先展示页面，后台 hydrate
 * - 只有 hydrate 完成后确认无用户，才跳登录
 * - 登录/登出跳转一律硬跳，避免 Next 软路由卡在「跳转登录…」
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthStore((s) => s.hydrated);
  const bootstrapped = useAuthStore((s) => s.bootstrapped);
  const bootstrapSync = useAuthStore((s) => s.bootstrapSync);
  const hydrate = useAuthStore((s) => s.hydrate);
  const isLogin = isLoginPath(pathname);

  useLayoutEffect(() => {
    bootstrapSync();
  }, [bootstrapSync]);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  // 登录后后台续期：闲置也不会因 access 15 分钟过期变成「有界面无数据」
  useEffect(() => {
    if (!user) {
      useAuthStore.getState().stopAuthKeepalive();
      return;
    }
    useAuthStore.getState().armAuthKeepalive();
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        useAuthStore.getState().armAuthKeepalive();
        void import("@/lib/api").then((m) => m.ensureFreshAccessToken()).catch(() => {});
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [user]);

  // hydrate / 内网 API 不可达时兜底：最多 2.5s，避免永久「正在恢复登录状态…」
  useEffect(() => {
    if (hydrated) return;
    const t = window.setTimeout(() => {
      const s = useAuthStore.getState();
      if (!s.hydrated) {
        useAuthStore.setState({ hydrated: true, hydrating: false });
      }
    }, 2500);
    return () => window.clearTimeout(t);
  }, [hydrated]);

  // 未登录不在登录页 → 立刻整页进登录（禁止软路由）
  useLayoutEffect(() => {
    if (!hydrated) return;
    if (!user && !isLogin) {
      hardNavigate("/login");
    } else if (user && isLogin) {
      hardNavigate("/dashboard");
    }
  }, [hydrated, user, isLogin]);

  // 已有用户（缓存或校验后）→ 直接进业务页
  if (user && !isLogin) {
    return <>{children}</>;
  }

  if (isLogin) {
    return <>{children}</>;
  }

  // 等待本地 bootstrap 或正在硬跳登录
  return (
    <div className="flex h-screen items-center justify-center bg-[#070b12] text-slate-400">
      <Loader2 className="mr-2 h-5 w-5 animate-spin text-emerald-400" />
      {!bootstrapped || !hydrated ? "正在恢复登录状态…" : "正在打开登录页…"}
    </div>
  );
}
