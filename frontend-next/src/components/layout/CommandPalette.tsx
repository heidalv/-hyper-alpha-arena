"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Search, CornerDownLeft } from "lucide-react";
import {
  LayoutDashboard, Brain, FlaskConical, LineChart,
  Settings2, Activity, Zap,
  Shield, Settings, Database as DBIcon,
  Server, ArrowRightLeft, CandlestickChart,
  FileText, Coins,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { softNavigate } from "@/lib/app-nav";

interface Cmd {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  keywords: string[];
}

const COMMANDS: Cmd[] = [
  { label: "仪表盘", href: "/dashboard", icon: LayoutDashboard, keywords: ["dashboard", "总览", "home"] },
  { label: "AI 策略", href: "/strategy", icon: Brain, keywords: ["strategy", "ai", "策略"] },
  { label: "VIP AI 选币", href: "/coin-select", icon: Coins, keywords: ["coin", "select", "选币", "vip"] },
  { label: "模拟交易", href: "/paper-trading", icon: FlaskConical, keywords: ["paper", "trading", "交易", "持仓"] },
  { label: "短线配置", href: "/scalp", icon: Zap, keywords: ["scalp", "短线", "config"] },
  { label: "长线配置", href: "/long", icon: Activity, keywords: ["long", "trend", "长线", "中线", "mid", "swing", "prompt", "提示词", "llm"] },
  { label: "提示词", href: "/long?tab=prompts", icon: Settings2, keywords: ["prompt", "提示词", "llm", "prompts"] },
  { label: "全市场数据中台", href: "/intel", icon: DBIcon, keywords: ["market", "intel", "行情", "oi", "chart", "kline", "图表", "k线"] },
  { label: "K 线", href: "/intel?tab=kline", icon: LineChart, keywords: ["chart", "kline", "图表", "k线", "candlestick"] },
  { label: "因子系统", href: "/factors", icon: FlaskConical, keywords: ["factor", "因子", "ic"] },
  { label: "交易所枢纽", href: "/exchange", icon: Server, keywords: ["exchange", "交易所", "account"] },
  { label: "套利中心", href: "/arbitrage", icon: ArrowRightLeft, keywords: ["arbitrage", "套利"] },
  { label: "Hyperliquid", href: "/hyperliquid", icon: CandlestickChart, keywords: ["hyperliquid", "hl", "dex"] },
  { label: "风控监控", href: "/risk", icon: Shield, keywords: ["risk", "风控"] },
  { label: "运维看板", href: "/ops", icon: Activity, keywords: ["ops", "运维", "看板", "heartbeat"] },
  { label: "报错中心", href: "/ops#ops-errors", icon: FileText, keywords: ["log", "日志", "报错", "system", "errors"] },
  { label: "设置", href: "/settings", icon: Settings, keywords: ["settings", "设置", "config"] },
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const router = useRouter();

  // Cmd+K / Ctrl+K 打开
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
      if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

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

  // 键盘导航
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
        const cmd = results[selectedIndex];
        if (cmd) {
          setOpen(false);
          setQuery("");
          softNavigate(cmd.href, (url) => router.push(url));
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, results, selectedIndex, router]);

  // 重置选中
  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  if (!open) return null;

  return (
    <>
      {/* 背景遮罩 */}
      <div
        className="fixed inset-0 bg-black/50 z-50"
        onClick={() => setOpen(false)}
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
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索页面或功能..."
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
                const isActive = i === selectedIndex;
                return (
                  <button
                    key={cmd.href}
                    onClick={() => {
                      setOpen(false);
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
