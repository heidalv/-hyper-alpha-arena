"use client";

/**
 * 套利中心共享布局 — 6 个一级视图的标签式导航（当前页高亮）
 *
 * 设计 §2：总览/车道/持仓/机会/风险与资金/配置。受 `output:"export"` 静态导出约束，
 * 车道详情用查询参数 `/arbitrage/lanes?lane=mm_asterdex`，不使用动态路由。
 * 本布局只渲染导航 + 子页面容器；各页面自行渲染 `PageShell` 页头。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import {
  LayoutDashboard, GitBranch, Wallet, Zap, ShieldAlert, Settings2,
} from "lucide-react";
import { cn } from "@/lib/utils";

const VIEWS = [
  { href: "/arbitrage", label: "总览", icon: LayoutDashboard },
  { href: "/arbitrage/lanes", label: "车道", icon: GitBranch },
  { href: "/arbitrage/positions", label: "持仓", icon: Wallet },
  { href: "/arbitrage/opportunities", label: "机会", icon: Zap },
  { href: "/arbitrage/risk", label: "风险与资金", icon: ShieldAlert },
  { href: "/arbitrage/config", label: "配置", icon: Settings2 },
];

export default function ArbitrageLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "";

  const isActive = (href: string) => {
    if (href === "/arbitrage") return pathname === "/arbitrage" || pathname === "/arbitrage/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <div className="flex min-h-[calc(100vh-57px)] flex-col">
      {/* 标签导航 */}
      <nav
        aria-label="套利中心视图"
        className="sticky top-0 z-20 flex items-center gap-1 overflow-x-auto border-b border-border/60 bg-background/85 px-4 pt-2 backdrop-blur md:px-6"
      >
        {VIEWS.map((v) => {
          const Icon = v.icon;
          const active = isActive(v.href);
          return (
            <Link
              key={v.href}
              href={v.href}
              className={cn(
                "flex flex-shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2 text-sm whitespace-nowrap transition-colors",
                active
                  ? "border-cyan-400 text-cyan-300"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
              aria-current={active ? "page" : undefined}
            >
              <Icon className="h-3.5 w-3.5" />
              {v.label}
            </Link>
          );
        })}
      </nav>

      {/* 子页面 */}
      <div className="flex-1">{children}</div>
    </div>
  );
}
