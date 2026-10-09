"use client";

import { useEffect, useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, CornerDownLeft } from "lucide-react";
import {
  LayoutDashboard, Brain, FlaskConical, LineChart,
  Settings2, Activity,
  Shield, Settings, Database as DBIcon,
  Server, ArrowRightLeft, CandlestickChart,
  FileText, Coins, PieChart, Radar, Boxes,
  Power, Bot, Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { softNavigate } from "@/lib/app-nav";
import { useUIStore } from "@/lib/stores/ui";

interface Cmd {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  keywords: string[];
}

const COMMANDS: Cmd[] = [
  { label: "仪表盘", href: "/dashboard", icon: LayoutDashboard, keywords: ["dashboard", "总览", "home"] },
  // [2026-10-03 用户指令] AI 策略页已移除（只留会话管理），会话管理 + 交易所管理 + 车道配置
  // 全部并入「交易总控」（归策略配置组）。原 /strategy、/exchange 保留为跳转桩，
  // 因此关键词把 strategy/ai/exchange/交易所 等老搜索词全部保留，命中后落到对应页签。
  { label: "交易总控", href: "/control", icon: Power,
    keywords: ["control", "总控", "strategy", "ai", "策略", "会话", "session", "停手", "自动交易"] },
  { label: "会话管理", href: "/control?tab=sessions", icon: Bot,
    keywords: ["session", "会话", "会话管理", "全自动", "full-auto", "启动", "停止", "strategy"] },
  { label: "VIP AI 选币", href: "/coin-select", icon: Coins, keywords: ["coin", "select", "选币", "vip"] },
  { label: "模拟交易", href: "/paper-trading", icon: FlaskConical, keywords: ["paper", "trading", "交易", "持仓"] },
  // [2026-10-01 合并] 原「Agent Wall 分析墙」已并入「Agent 监控」（侧栏归入「市场 & 分析」）：
  // 单入口 /agent-monitor，默认落在画布 Tab；关键词保留 wall/canvas/画布/滚屏/链路，
  // 老搜索词照样命中（不再单列第二条）。
  { label: "Agent 监控", href: "/agent-monitor", icon: Radar,
    keywords: ["agent", "monitor", "wall", "canvas", "画布", "滚屏", "链路", "断链", "主脑", "brain", "监控"] },
  // [2026-10-01 合并] 「中线配置」「长线配置」并入原 AI 策略页；[2026-10-03] 随 AI 策略页移除，
  // 迁到「交易总控 → 车道配置」（?cfg= 展开并滚动到对应卡；?sub= 指定卡内子页签）。
  { label: "中线配置", href: "/control?tab=lanes&cfg=mid", icon: Boxes, keywords: ["mid", "中线", "配置", "因子路由", "日内波段", "swing"] },
  { label: "长线配置", href: "/control?tab=lanes&cfg=long", icon: Activity, keywords: ["long", "trend", "长线", "配置", "chandelier"] },
  { label: "提示词", href: "/control?tab=lanes&cfg=long&sub=prompts", icon: Settings2, keywords: ["prompt", "提示词", "llm", "prompts", "mid", "long", "中线", "长线"] },
  { label: "全市场数据中台", href: "/intel", icon: DBIcon, keywords: ["market", "intel", "行情", "oi", "chart", "kline", "图表", "k线"] },
  { label: "K 线", href: "/intel?tab=kline", icon: LineChart, keywords: ["chart", "kline", "图表", "k线", "candlestick"] },
  // [2026-10-01 合并] 「因子研究中心」已并入「因子系统」（页内 Tab「研究中心」，深链 ?tab=lab）：
  // 单入口 /factors；关键词合并保留，老搜索词（研究中心/文献/假设/五agent）照样命中。
  { label: "因子系统", href: "/factors", icon: FlaskConical,
    keywords: ["factor", "factors-lab", "因子", "因子研究", "研究中心", "文献", "假设", "ic", "alpha", "五agent"] },
  // [2026-10-03] 交易所管理已并入「交易总控」（页签「交易所管理」）；
  // 老搜索词 exchange/交易所/account/凭证 保留，命中即落到该页签。
  { label: "交易所管理", href: "/control?tab=exchange", icon: Server,
    keywords: ["exchange", "交易所", "交易所枢纽", "account", "凭证", "api", "监控", "积分"] },
  // [2026-10-03 用户指令] 「周期报告 并入 智能学习」：内容成为智能学习中心的 Tab（?tab=reports）。
  // 智能学习此前不在面板里，本次一并补上两条入口；旧词 report/报告/日报/周报 全保留。
  { label: "智能学习", href: "/intelligent-learning", icon: Workflow,
    keywords: ["learning", "学习", "智能学习", "wisdom", "血缘", "三通道", "调度"] },
  { label: "周期报告", href: "/intelligent-learning?tab=reports", icon: FileText,
    keywords: ["report", "reports", "报告", "周期报告", "日报", "周报", "车道复盘", "复盘"] },
  { label: "套利中心", href: "/arbitrage", icon: ArrowRightLeft, keywords: ["arbitrage", "套利"] },
  { label: "Hyperliquid", href: "/hyperliquid", icon: CandlestickChart, keywords: ["hyperliquid", "hl", "dex"] },
  { label: "风控监控", href: "/risk", icon: Shield, keywords: ["risk", "风控"] },
  { label: "资本与边际", href: "/capital-margin", icon: PieChart, keywords: ["capital", "margin", "资本", "边际", "分配", "kpi"] },
  { label: "运维看板", href: "/ops", icon: Activity, keywords: ["ops", "运维", "看板", "heartbeat"] },
  { label: "报错中心", href: "/ops#ops-errors", icon: FileText, keywords: ["log", "日志", "报错", "system", "errors"] },
  { label: "设置", href: "/settings", icon: Settings, keywords: ["settings", "设置", "config"] },
];

export function CommandPalette() {
  const router = useRouter();
  // R5-1：面板状态/查询由 ui store 驱动（TopBar 搜索框与 Ctrl+K 共用）
  const open = useUIStore((s) => s.commandPaletteOpen);
  const query = useUIStore((s) => s.paletteQuery);
  const openPalette = useUIStore((s) => s.openCommandPalette);
  const closePalette = useUIStore((s) => s.closeCommandPalette);
  const setQuery = useUIStore((s) => s.setPaletteQuery);
  const [selectedIndex, setSelectedIndex] = useState(0);

  // Cmd+K / Ctrl+K 打开
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        open ? closePalette() : openPalette();
      }
      if (e.key === "Escape") {
        closePalette();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, openPalette, closePalette]);

  const filtered = useCallback(() => {
    if (!query.trim()) return COMMANDS;
    const q = query.toLowerCase();
    return COMMANDS.filter((c) =>
      c.label.toLowerCase().includes(q) ||
      c.href.includes(q) ||
      c.keywords.some((k) => k.includes(q))
    );
  }, [query]);

  const results = filtered();
  // 渲染时钳位选中索引（查询变化导致结果缩短时兜底，避免 effect 内同步 setState）
  const activeIndex = results.length > 0 ? Math.min(selectedIndex, results.length - 1) : 0;

  // 键盘导航（↑↓ + Enter）
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const cmd = results[activeIndex];
        if (cmd) {
          closePalette();
          setQuery("");
          softNavigate(cmd.href, (url) => router.push(url));
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, results, activeIndex, router, closePalette, setQuery]);

  if (!open) return null;

  return (
    <>
      {/* 背景遮罩 */}
      <div
        className="fixed inset-0 bg-black/50 z-50"
        onClick={closePalette}
      />

      {/* 面板 */}
      <div className="fixed left-1/2 top-[20%] -translate-x-1/2 w-full max-w-lg z-50">
        <div className="bg-card border border-border rounded-lg shadow-xl overflow-hidden">
          {/* 搜索输入 */}
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
            <Search className="w-4 h-4 text-muted-foreground" />
            <input
              autoFocus
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelectedIndex(0);
              }}
              placeholder="搜索页面或功能..."
              aria-label="搜索页面或功能"
              className="flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
          </div>

          {/* 结果列表 */}
          <div className="max-h-80 overflow-y-auto py-2">
            {results.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">无匹配结果</div>
            ) : (
              results.map((cmd, i) => {
                const Icon = cmd.icon;
                const isActive = i === activeIndex;
                return (
                  <button
                    key={cmd.href}
                    onClick={() => {
                      closePalette();
                      setQuery("");
                      softNavigate(cmd.href, (url) => router.push(url));
                    }}
                    onMouseEnter={() => setSelectedIndex(i)}
                    className={cn(
                      "w-full flex items-center gap-3 px-4 py-2 text-sm transition-colors",
                      isActive ? "bg-primary/10 text-primary" : "text-foreground hover:bg-muted/50"
                    )}
                  >
                    <Icon className="w-4 h-4 flex-shrink-0" />
                    <span className="flex-1 text-left">{cmd.label}</span>
                    {isActive && <CornerDownLeft className="w-3 h-3 text-muted-foreground" />}
                  </button>
                );
              })
            )}
          </div>

          {/* 底部提示 */}
          <div className="flex items-center justify-between px-4 py-2 border-t border-border text-[10px] text-muted-foreground">
            <div className="flex gap-3">
              <span>↑↓ 导航</span>
              <span>↵ 选择</span>
              <span>ESC 关闭</span>
            </div>
            <span>Heidalv Alpha Arena</span>
          </div>
        </div>
      </div>
    </>
  );
}
