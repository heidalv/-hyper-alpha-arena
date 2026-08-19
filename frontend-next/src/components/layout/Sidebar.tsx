"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo } from "react";
import {
  LayoutDashboard, Brain, FlaskConical,
  Database as DBIcon, TrendingUp,
  Shield, Settings, Activity, ChevronLeft, Zap,
  Server, ArrowRightLeft,
  FlaskConical as Factor, FileText,
  Radar, Coins, Workflow, Cpu, Boxes,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { shouldHandleNavClick, softNavigate } from "@/lib/app-nav";
import { useAuthStore } from "@/lib/stores/auth";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  vipOnly?: boolean;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: "交易核心",
    items: [
      { href: "/dashboard", label: "仪表盘", icon: LayoutDashboard },
      { href: "/strategy", label: "AI 策略", icon: Brain },
      { href: "/coin-select", label: "VIP AI 选币", icon: Coins, vipOnly: true },
      { href: "/agent-monitor", label: "Agent 监控", icon: Radar },
      { href: "/paper-trading", label: "模拟交易", icon: FlaskConical },
      { href: "/live-trading", label: "实盘交易", icon: TrendingUp },
      { href: "/arbitrage", label: "套利中心", icon: ArrowRightLeft },
      // K 线已并入全市场数据中台（/intel?tab=kline）
    ],
  },
  {
    title: "策略配置",
    items: [
      { href: "/scalp", label: "短线配置", icon: Zap },
      { href: "/mid", label: "中线配置", icon: Boxes },
      { href: "/long", label: "长线配置", icon: Activity },
    ],
  },
  {
    title: "交易所",
    items: [
      { href: "/exchange", label: "交易所管理", icon: Server },
    ],
  },
  {
    title: "市场 & 分析",
    items: [
      { href: "/intel", label: "全市场数据中台", icon: DBIcon },
      { href: "/factors", label: "因子系统", icon: Factor },
      { href: "/reports", label: "周期报告", icon: TrendingUp },
      { href: "/intelligent-learning", label: "智能学习", icon: Workflow },
      { href: "/compute", label: "算力中心", icon: Cpu },
    ],
  },
  {
    title: "系统",
    items: [
      { href: "/risk", label: "风控监控", icon: Shield },
      { href: "/ops", label: "运维看板", icon: Activity },
      { href: "/ops#ops-errors", label: "报错中心", icon: FileText },
      { href: "/settings", label: "设置", icon: Settings },
    ],
  },
];

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const showVip = useMemo(() => {
    const tier = (user?.tier || "").toLowerCase();
    const role = (user?.role || "").toLowerCase();
    return tier === "vip" || role === "admin";
  }, [user]);

  const groups = useMemo(
    () =>
      NAV_GROUPS.map((g) => ({
        ...g,
        items: g.items.filter((it) => !it.vipOnly || showVip),
      })),
    [showVip]
  );

  return (
    <aside
      className={cn(
        "relative z-10 flex flex-col bg-sidebar border-r border-sidebar-border backdrop-blur-md transition-all duration-200",
        collapsed ? "w-16" : "w-[216px]"
      )}
    >
      <div className="flex items-center h-14 px-4 border-b border-sidebar-border flex-shrink-0">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="w-8 h-8 rounded-[10px] bg-gradient-to-br from-cyan-400 to-violet-500 flex items-center justify-center flex-shrink-0 shadow-[0_0_18px_rgba(34,211,238,0.4),0_0_30px_rgba(139,92,246,0.25)]">
            <span className="text-[#041018] font-bold text-sm">α</span>
          </div>
          {!collapsed && (
            <div className="overflow-hidden">
              <div className="text-sm font-bold text-foreground truncate">Heidalv Alpha</div>
              <div className="text-[10px] text-muted-foreground truncate">量化交易终端</div>
            </div>
          )}
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto py-2">
        {groups.map((group) => (
          <div key={group.title} className="mb-3">
            {!collapsed && (
              <div className="px-4 mb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                {group.title}
              </div>
            )}
            {group.items.map((item) => {
              const Icon = item.icon;
              const hrefPath = item.href.split(/[?#]/)[0] || "/";
              const isActive =
                pathname === hrefPath ||
                (hrefPath !== "/" && !!pathname?.startsWith(hrefPath + "/"));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  prefetch={false}
                  onClick={(e) => {
                    if (!shouldHandleNavClick(e)) return;
                    e.preventDefault();
                    // 同页锚点：直接滚到报错区
                    if (
                      item.href.includes("#") &&
                      pathname === hrefPath
                    ) {
                      const id = item.href.split("#")[1];
                      const el = id ? document.getElementById(id) : null;
                      if (el) {
                        el.scrollIntoView({ behavior: "smooth", block: "start" });
                        return;
                      }
                    }
                    softNavigate(item.href, (url) => router.push(url));
                  }}
                  className={cn(
                    "flex items-center gap-3 px-4 py-2 text-sm transition-colors relative group",
                    isActive
                      ? "text-primary bg-primary/10"
                      : "text-sidebar-foreground/70 hover:text-foreground hover:bg-sidebar-accent/50",
                    collapsed && "justify-center px-2"
                  )}
                  title={collapsed ? item.label : undefined}
                >
                  {isActive && (
                    <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-gradient-to-b from-cyan-400 to-violet-500 shadow-[0_0_10px_rgba(34,211,238,0.7)]" />
                  )}
                  <Icon className="w-4 h-4 flex-shrink-0" />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <button
        onClick={onToggle}
        className="flex items-center justify-center h-10 border-t border-sidebar-border text-muted-foreground hover:text-foreground hover:bg-sidebar-accent/50 transition-colors flex-shrink-0"
      >
        <ChevronLeft className={cn("w-4 h-4 transition-transform", collapsed && "rotate-180")} />
      </button>
    </aside>
  );
}
