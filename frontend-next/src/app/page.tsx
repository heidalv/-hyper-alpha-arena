"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth";

/**
 * 首页：静态导出下不用 next/navigation redirect（Electron file:// 更稳）。
 * 已登录 → dashboard；未登录 → login（由 AuthGate 也会拦）。
 */
export default function Home() {
  const router = useRouter();
  const { user, hydrated, hydrate } = useAuthStore();

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (!hydrated) return;
    router.replace(user ? "/dashboard" : "/login");
  }, [hydrated, user, router]);

  return (
    <div className="flex h-screen items-center justify-center bg-[#070b12] text-slate-400">
      <Loader2 className="mr-2 h-5 w-5 animate-spin text-emerald-400" />
      正在进入…
    </div>
  );
}
