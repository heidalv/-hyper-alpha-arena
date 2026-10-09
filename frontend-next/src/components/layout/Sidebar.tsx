"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo } from "react";
import {
  LayoutDashboard, Brain, FlaskConical,
  Database as DBIcon, TrendingUp,
  Shield, Settings, Activity, ChevronLeft,
  Server, ArrowRightLeft,
  FlaskConical as Factor, FileText,
  Radar, Coins, Workflow, Cpu, PieChart, Radio, Power,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { shouldHandleNavClick, softNavigate } from "@/lib/app-nav";
import { useAuthStore } from "@/lib/stores/auth";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  vipOnly?: boolean;
  /**
   * [2026-09-17] 隐藏该入口（不出现在侧栏，但路由仍可达）。
   *
   * 用途：用户要求「**完全停止整个套利中心的运行，隐藏**」。
   * 与 `vipOnly` 同构（同一处 filter 处理），因此不引入新机制。
   *
   * 为什么用字段而不是 env 开关：
   *   `NEXT_PUBLIC_*` 在客户端是**构建期内联**的 —— 运行时改 `.env` 不生效，
   *   会出现"看着隐藏了其实没隐藏"。这里改用**源码常量**，语义明确、可审计。
   *
   * 恢复：删掉该条目的 `hidden: true`（或整条恢复）并重新构建前端。
   */
  hidden?: boolean;
  /** [h665b] 子页面:缩进渲染在父级下方(如"实盘高频交易"→"模拟高频交易")。 */
  children?: NavItem[];
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
      // [2026-09-20 F250] 中短期高频交易：**独立模块**，以 L1 赛道为借鉴实现，
      // 但不属于套利中心（`/api/hft/*` 独立命名空间；/hft 独立页面）。
      // [2026-10-04 用户重命名] 父级「实盘高频交易」(原「实盘做市」)、
      // 子级「模拟高频交易」(原「模拟做市」，更早叫「高频交易」)。
      {
        href: "/hft/live",
        label: "实盘高频交易",
        icon: TrendingUp,
        children: [{ href: "/hft", label: "模拟高频交易", icon: Radio }],
      },
      // [2026-10-01 用户指令] 本组只留**交易执行面**入口：
      //   · 「AI 策略」+「VIP AI 选币」→ 移到「策略配置」组；
      //   · 「Agent 监控」（含原 Agent Wall 画布）→ 移到「市场 & 分析」组；
      //   · 「模拟交易」→ 并入下面的「实盘交易」，作为子模块缩进显示（沿用 h665b 的
      //     父/子模式「实盘高频交易 → 模拟高频交易」：父子都是独立路由，/paper-trading 仍可直达）。
      // 对应页面页头面包屑已同步（/strategy、/coin-select、/agent-monitor、/paper-trading）。
      {
        href: "/live-trading",
        label: "实盘交易",
        icon: TrendingUp,
        children: [{ href: "/paper-trading", label: "模拟交易", icon: FlaskConical }],
      },
      // [2026-09-17] 套利中心按用户要求**停止并隐藏**（车道 mm_asterdex 停机、
      // 调度任务已注销）。入口不显示，但 `/arbitrage` 路由仍可直达（便于排查/恢复）。
      // 恢复：删掉 `hidden: true` 并重新构建前端。
      //
      // [2026-09-20] 用户重申：新的高频模块**不用套利中心**。⇒ 本条目**保持隐藏**，
      // 新模块走独立入口 `/hft`（见下方「交易核心」组）。不要因为"想让新东西可见"
      // 而把这个入口打开 —— 那是两回事。
      { href: "/arbitrage", label: "套利中心", icon: ArrowRightLeft, hidden: true },
      // K 线已并入全市场数据中台（/intel?tab=kline）
    ],
  },
  {
    title: "策略配置",
    items: [
      // [2026-10-03 用户指令] 「ai 策略里只保留会话管理，其他的都没用。然后把会话管理并入交易总控，
      // 交易所管理页并入交易总控。交易总控放在策略配置组，原来的 ai 策略就没有了」
      // ⇒ 本组现在只有：交易总控（= 总控 / 会话管理 / 交易所管理 / 车道配置 四区）+ VIP AI 选币。
      // AI 策略入口已移除（旧路由 /strategy 为跳转桩 → /control?tab=sessions）；
      // 交易所管理入口已移除（旧路由 /exchange 为跳转桩 → /control?tab=exchange）。
      { href: "/control", label: "交易总控", icon: Power },
      { href: "/coin-select", label: "VIP AI 选币", icon: Coins, vipOnly: true },
      // [2026-09-17] 「短线配置」已随短线车道停用移除（SCALP_OPEN_DISABLED=true，
      // 后端 /api/scalp-config 同步下架；/scalp 页面已删除）。
      // [2026-10-01 用户指令] 「中线配置」「长线配置」并入原「AI 策略」作为页内卡片；
      // [2026-10-03] 随本次重构迁到「交易总控」第三区（深链 /control?tab=lanes&cfg=mid|long）。
      // 旧路由 /mid、/long 保留为跳转桩。
    ],
  },
  {
    title: "市场 & 分析",
    items: [
      { href: "/intel", label: "全市场数据中台", icon: DBIcon },
      { href: "/factors", label: "因子系统", icon: Factor },
      // [2026-10-01 用户指令] 「因子研究中心」已并入「因子系统」页（Tab「研究中心」，
      // 深链 /factors?tab=lab；旧路由 /factors-lab 为跳转桩），侧栏不再单列入口。
      // [2026-10-03 用户指令] 「周期报告 并入 智能学习」⇒ 本组不再单列「周期报告」入口，
      // 内容成为智能学习中心的页内 Tab（深链 /intelligent-learning?tab=reports；
      // 旧路由 /reports 保留为跳转桩）。
      { href: "/intelligent-learning", label: "智能学习", icon: Workflow },
      { href: "/compute", label: "算力中心", icon: Cpu },
      // [2026-10-01 用户指令] Agent 监控（含原 Agent Wall 画布）归入本组。
      // 画布 = 页内第一个 Tab「分析墙（画布）」；深链 /agent-monitor?tab=wall。
      { href: "/agent-monitor", label: "Agent 监控", icon: Radar },
    ],
  },
  {
    title: "系统",
    items: [
      { href: "/risk", label: "风控监控", icon: Shield },
      { href: "/capital-margin", label: "资本与边际", icon: PieChart },
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
        // `hidden` 优先于 `vipOnly`：隐藏条目对任何人都不显示（含 admin）。
        // 2026-09-17：套利中心入口即由此隐藏（车道已停机）。
        items: g.items.filter((it) => !it.hidden && (!it.vipOnly || showVip)),
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
              const childItems = (item.children ?? []).filter((c) => !c.hidden && (!c.vipOnly || showVip));
              return (
                <div key={item.href}>
                  <Link
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
                  {/* [h665b] 子页面:缩进在父级下方,精确匹配激活 */}
                  {!collapsed && childItems.length > 0 && (
                    <div className="mt-0.5 space-y-0.5">
                      {childItems.map((child) => {
                        const CIcon = child.icon;
                        const childPath = child.href.split(/[?#]/)[0] || "/";
                        const childActive = pathname === childPath;
                        return (
                          <Link
                            key={child.href}
                            href={child.href}
                            prefetch={false}
                            onClick={(e) => {
                              if (!shouldHandleNavClick(e)) return;
                              e.preventDefault();
                              softNavigate(child.href, (url) => router.push(url));
                            }}
                            className={cn(
                              "flex items-center gap-2 pl-9 py-1.5 text-[13px] transition-colors relative",
                              childActive
                                ? "text-primary bg-primary/10"
                                : "text-sidebar-foreground/60 hover:text-foreground hover:bg-sidebar-accent/50"
                            )}
                          >
                            {childActive && (
                              <span className="absolute left-0 top-1 bottom-1 w-[2px] rounded-r bg-cyan-400/80" />
                            )}
                            <CIcon className="w-3.5 h-3.5 flex-shrink-0" />
                            <span className="truncate">{child.label}</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </div>
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

