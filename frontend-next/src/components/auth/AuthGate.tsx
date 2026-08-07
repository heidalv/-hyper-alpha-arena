"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth";

/**
 * 路由守卫：未登录跳 /login；已登录访问 /login 则去 /dashboard。
 * /login 页面本身不包 AppShell 侧栏。
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, hydrated, hydrate } = useAuthStore();
  const isLogin = pathname === "/login" || pathname?.startsWith("/login/");

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!hydrated) return;
    if (!user && !isLogin) {
      router.replace("/login");
    } else if (user && isLogin) {
      router.replace("/dashboard");
    }
  }, [hydrated, user, isLogin, router]);

  if (!hydrated) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#070b12] text-slate-400">
        <Loader2 className="mr-2 h-5 w-5 animate-spin text-emerald-400" />
        正在恢复登录状态…
      </div>
    );
  }

  if (!user && !isLogin) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#070b12] text-slate-400">
        <Loader2 className="mr-2 h-5 w-5 animate-spin text-emerald-400" />
        跳转登录…
      </div>
    );
  }

  if (isLogin) {
    return <>{children}</>;
  }

  return <>{children}</>;
}
