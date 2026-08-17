"use client";

import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { cn } from "@/lib/utils";
import {
  LineChart,
  BarChart3,
  CandlestickChart,
  PieChart,
  Activity,
  Download,
  SlidersHorizontal,
  Grid3x3,
} from "lucide-react";

type Period = "日线" | "周线" | "月线";

// 月度 P&L 柱（近 6 月，涨绿跌红，末柱极光渐变）
const PNL_BARS = [
  { x: 22, y: 120, h: 70, fill: "rgba(52,211,153,0.55)" },
  { x: 68, y: 150, h: 40, fill: "rgba(251,113,133,0.55)" },
  { x: 114, y: 100, h: 90, fill: "rgba(52,211,153,0.65)" },
  { x: 160, y: 128, h: 62, fill: "rgba(52,211,153,0.75)" },
  { x: 206, y: 88, h: 102, fill: "rgba(34,211,238,0.6)" },
  { x: 252, y: 70, h: 120, fill: "url(#chartsGradCyanViolet)" },
];
const PNL_MONTHS = ["3月", "4月", "5月", "6月", "7月", "8月"];

// BTC-USD 4h 烛台（16 根，涨绿跌红）
const CANDLES = [
  { x: 20, y1: 120, y2: 150, by: 126, bh: 14, up: false },
  { x: 60, y1: 110, y2: 142, by: 118, bh: 12, up: false },
  { x: 100, y1: 100, y2: 136, by: 104, bh: 24, up: true },
  { x: 140, y1: 96, y2: 128, by: 100, bh: 18, up: true },
  { x: 180, y1: 90, y2: 126, by: 96, bh: 16, up: false },
  { x: 220, y1: 84, y2: 120, by: 92, bh: 14, up: false },
  { x: 260, y1: 78, y2: 116, by: 82, bh: 26, up: true },
  { x: 300, y1: 70, y2: 104, by: 74, bh: 22, up: true },
  { x: 340, y1: 66, y2: 102, by: 72, bh: 18, up: false },
  { x: 380, y1: 58, y2: 96, by: 62, bh: 26, up: true },
  { x: 420, y1: 50, y2: 88, by: 56, bh: 18, up: false },
  { x: 460, y1: 44, y2: 82, by: 48, bh: 26, up: true },
  { x: 500, y1: 38, y2: 74, by: 42, bh: 22, up: true },
  { x: 540, y1: 32, y2: 70, by: 38, bh: 18, up: false },
  { x: 580, y1: 28, y2: 64, by: 32, bh: 24, up: true },
  { x: 620, y1: 20, y2: 58, by: 24, bh: 28, up: true },
];

// 6×6 因子相关性热力矩阵（极光五色）
const CORR = [
  ["rgba(34,211,238,0.8)", "rgba(139,92,246,0.45)", "rgba(139,92,246,0.2)", "rgba(52,211,153,0.25)", "rgba(52,211,153,0.55)", "rgba(34,211,238,0.35)"],
  ["rgba(139,92,246,0.45)", "rgba(34,211,238,0.75)", "rgba(52,211,153,0.4)", "rgba(251,113,133,0.3)", "rgba(139,92,246,0.3)", "rgba(251,191,36,0.35)"],
  ["rgba(139,92,246,0.2)", "rgba(52,211,153,0.4)", "rgba(34,211,238,0.7)", "rgba(251,191,36,0.3)", "rgba(251,113,133,0.25)", "rgba(52,211,153,0.5)"],
  ["rgba(52,211,153,0.25)", "rgba(251,113,133,0.3)", "rgba(251,191,36,0.3)", "rgba(34,211,238,0.6)", "rgba(52,211,153,0.35)", "rgba(139,92,246,0.45)"],
  ["rgba(52,211,153,0.55)", "rgba(139,92,246,0.3)", "rgba(251,113,133,0.25)", "rgba(52,211,153,0.35)", "rgba(34,211,238,0.55)", "rgba(251,113,133,0.2)"],
  ["rgba(34,211,238,0.35)", "rgba(251,191,36,0.35)", "rgba(52,211,153,0.5)", "rgba(139,92,246,0.45)", "rgba(251,113,133,0.2)", "rgba(34,211,238,0.7)"],
];
const CORR_LABELS = ["mom", "ic", "vol", "liq", "snt", "fun"];

// 因子 IC 分布直方
const IC_BARS = [
  { x: 20, y: 95, h: 45, fill: "rgba(139,92,246,0.4)" },
  { x: 44, y: 80, h: 60, fill: "rgba(139,92,246,0.55)" },
  { x: 68, y: 60, h: 80, fill: "rgba(139,92,246,0.7)" },
  { x: 92, y: 40, h: 100, fill: "rgba(34,211,238,0.75)" },
  { x: 116, y: 55, h: 85, fill: "rgba(34,211,238,0.6)" },
  { x: 140, y: 70, h: 70, fill: "rgba(34,211,238,0.45)" },
  { x: 164, y: 88, h: 52, fill: "rgba(52,211,153,0.5)" },
  { x: 188, y: 78, h: 62, fill: "rgba(52,211,153,0.65)" },
  { x: 212, y: 98, h: 42, fill: "rgba(52,211,153,0.5)" },
  { x: 236, y: 108, h: 32, fill: "rgba(251,191,36,0.45)" },
  { x: 260, y: 115, h: 25, fill: "rgba(251,191,36,0.4)" },
  { x: 284, y: 120, h: 20, fill: "rgba(251,191,36,0.35)" },
];

export default function ChartsPage() {
  const [period, setPeriod] = useState<Period>("日线");

  return (
    <div className="p-4 space-y-4">
      {/* 全局渐变定义（极光青 → 紫） */}
      <svg width="0" height="0" className="absolute" aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id="chartsGradCyanViolet" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#22D3EE" />
            <stop offset="1" stopColor="#8B5CF6" />
          </linearGradient>
        </defs>
      </svg>

      {/* page-head 标题区 */}
      <PageHeader
        icon={<LineChart className="w-4 h-4" />}
        title="图表中心"
        subtitle="权益曲线 · K 线 · 因子分布 · 相关性 —— Aurora 图表语言展示"
        breadcrumb={[{ label: "市场 & 分析" }, { label: "图表中心" }]}
        refreshHint="下次刷新 60s · 数据延迟 1.8s"
        actions={
          <>
            <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-white/5 border border-border">
              {(["日线", "周线", "月线"] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPeriod(p)}
                  className={cn(
                    "h-7 px-3 rounded-md text-xs transition-colors",
                    period === p
                      ? "bg-gradient-to-br from-cyan-400/25 to-violet-500/25 text-foreground shadow-[0_0_12px_rgba(34,211,238,0.15)]"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {p}
                </button>
              ))}
            </div>
            <Button variant="ghost" size="sm" className="text-xs">
              <SlidersHorizontal className="w-3.5 h-3.5" />
              叠加指标
            </Button>
            <Button variant="outline" size="sm" className="btn-glow text-xs">
              <Download className="w-3.5 h-3.5" />
              导出 PNG
            </Button>
          </>
        }
      />

      {/* 玻璃卡片 grid（2:1）：权益曲线 + 月度 P&L */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="p-4 glass lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <LineChart className="w-4 h-4 text-cyan-300" />
              权益曲线
            </div>
            <span className="text-xs font-mono text-muted-foreground">近 90 天 · 模拟账户</span>
          </div>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2 mb-3">
            <div>
              <div className="text-xs text-muted-foreground">当前权益</div>
              <div className="font-mono text-xl font-bold tabular-nums">$12,480.52</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">区间收益</div>
              <div className="font-mono text-xl font-bold tabular-nums text-profit">+24.8%</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">最大回撤</div>
              <div className="font-mono text-xl font-bold tabular-nums text-loss">-6.2%</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">夏普比率</div>
              <div className="font-mono text-xl font-bold tabular-nums">1.86</div>
            </div>
          </div>
          <svg viewBox="0 0 760 240" className="w-full h-auto">
            <defs>
              <linearGradient id="chartsEqFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="rgba(34,211,238,0.30)" />
                <stop offset="1" stopColor="rgba(34,211,238,0)" />
              </linearGradient>
            </defs>
            <g stroke="rgba(255,255,255,0.06)" strokeWidth="1">
              <line x1="0" y1="40" x2="760" y2="40" />
              <line x1="0" y1="90" x2="760" y2="90" />
              <line x1="0" y1="140" x2="760" y2="140" />
              <line x1="0" y1="190" x2="760" y2="190" />
            </g>
            <path
              d="M0 205 L30 200 L60 196 L90 199 L120 188 L150 184 L180 176 L210 180 L240 168 L270 160 L300 164 L330 152 L360 148 L390 154 L420 142 L450 136 L480 128 L510 132 L540 118 L570 110 L600 116 L630 98 L660 90 L690 84 L720 72 L760 58 L760 240 L0 240 Z"
              fill="url(#chartsEqFill)"
            />
            <path
              d="M0 205 L30 200 L60 196 L90 199 L120 188 L150 184 L180 176 L210 180 L240 168 L270 160 L300 164 L330 152 L360 148 L390 154 L420 142 L450 136 L480 128 L510 132 L540 118 L570 110 L600 116 L630 98 L660 90 L690 84 L720 72 L760 58"
              fill="none"
              stroke="#22D3EE"
              strokeWidth="2"
              strokeLinecap="round"
            />
            <circle cx="760" cy="58" r="4" fill="#22D3EE" opacity="0.9" />
            <circle cx="760" cy="58" r="9" fill="none" stroke="#22D3EE" opacity="0.3" />
            <g fontSize="10" fill="#6E7B98" fontFamily="JetBrains Mono, monospace">
              <text x="6" y="30">$13,000</text>
              <text x="6" y="80">$12,500</text>
              <text x="6" y="130">$12,000</text>
              <text x="6" y="180">$11,500</text>
              <text x="6" y="230">$11,000</text>
              <text x="690" y="228">今日</text>
            </g>
          </svg>
        </Card>

        <Card className="p-4 glass">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <BarChart3 className="w-4 h-4 text-violet-300" />
              月度 P&L
            </div>
            <span className="text-xs font-mono text-muted-foreground">近 6 月</span>
          </div>
          <svg viewBox="0 0 320 220" className="w-full h-auto">
            <g stroke="rgba(255,255,255,0.06)" strokeWidth="1">
              <line x1="0" y1="40" x2="320" y2="40" />
              <line x1="0" y1="110" x2="320" y2="110" />
              <line x1="0" y1="180" x2="320" y2="180" />
            </g>
            {PNL_BARS.map((b, i) => (
              <rect
                key={i}
                x={b.x}
                y={b.y}
                width="32"
                height={b.h}
                rx="4"
                fill={b.fill}
                opacity={i === PNL_BARS.length - 1 ? 0.75 : undefined}
              />
            ))}
            <g fontSize="10" fill="#6E7B98" fontFamily="JetBrains Mono, monospace">
              {PNL_MONTHS.map((m, i) => (
                <text key={m} x={16 + i * 46} y="230">
                  {m}
                </text>
              ))}
            </g>
          </svg>
          <div className="mt-2">
            <div className="flex items-center justify-between py-1.5 border-b border-dashed border-white/10">
              <span className="text-xs text-muted-foreground">盈利月 / 亏损月</span>
              <span className="font-mono text-xs font-semibold tabular-nums text-profit">5 / 1</span>
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-xs text-muted-foreground">累计净 P&L</span>
              <span className="font-mono text-xs font-semibold tabular-nums text-profit">+$2,406.18</span>
            </div>
          </div>
        </Card>
      </div>

      {/* 玻璃卡片 grid（3:2）：K 线 + 因子相关性 */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="p-4 glass lg:col-span-3">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <CandlestickChart className="w-4 h-4 text-cyan-300" />
              BTC-USD K 线
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-muted-foreground">4h · 极光烛台</span>
              <Badge variant="secondary" className="text-xs text-cyan-300 bg-cyan-400/10 border-cyan-400/20">
                现货
              </Badge>
            </div>
          </div>
          <svg viewBox="0 0 640 260" className="w-full h-auto">
            <g stroke="rgba(255,255,255,0.06)" strokeWidth="1">
              <line x1="0" y1="52" x2="640" y2="52" />
              <line x1="0" y1="104" x2="640" y2="104" />
              <line x1="0" y1="156" x2="640" y2="156" />
              <line x1="0" y1="208" x2="640" y2="208" />
            </g>
            {CANDLES.map((c) => (
              <g key={c.x}>
                <line
                  x1={c.x}
                  y1={c.y1}
                  x2={c.x}
                  y2={c.y2}
                  stroke={c.up ? "#34D399" : "#FB7185"}
                  strokeWidth="1"
                  strokeLinecap="round"
                />
                <rect
                  x={c.x - 7}
                  y={c.by}
                  width="14"
                  height={c.bh}
                  rx="2"
                  fill={c.up ? "rgba(52,211,153,0.75)" : "rgba(251,113,133,0.7)"}
                />
              </g>
            ))}
            <path
              d="M0 96 L40 90 L80 84 L120 88 L160 76 L200 70 L240 66 L280 60 L320 54 L360 50 L400 44 L440 40 L480 34 L520 30 L560 26 L600 22 L640 18"
              fill="none"
              stroke="#22D3EE"
              strokeWidth="1.5"
              strokeDasharray="4 3"
              opacity="0.8"
            />
            <g fontSize="10" fill="#6E7B98" fontFamily="JetBrains Mono, monospace">
              <text x="6" y="30">$71.2k</text>
              <text x="6" y="86">$70.6k</text>
              <text x="6" y="142">$70.0k</text>
              <text x="6" y="198">$69.4k</text>
            </g>
          </svg>
        </Card>

        <Card className="p-4 glass lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Grid3x3 className="w-4 h-4 text-violet-300" />
              因子相关性
            </div>
            <span className="text-xs font-mono text-muted-foreground">top 6 因子</span>
          </div>
          <svg viewBox="0 0 300 260" className="w-full h-auto">
            {CORR.map((row, r) =>
              row.map((color, c) => (
                <rect
                  key={`${r}-${c}`}
                  x={40 + c * 40}
                  y={10 + r * 40}
                  width="36"
                  height="36"
                  rx="4"
                  fill={color}
                />
              ))
            )}
            <g fontSize="10" fill="#6E7B98" fontFamily="JetBrains Mono, monospace">
              {CORR_LABELS.map((l, i) => (
                <text key={`x-${i}`} x={42 + i * 40} y="238">
                  {l}
                </text>
              ))}
              {CORR_LABELS.map((l, i) => (
                <text key={`y-${i}`} x="8" y={30 + i * 40}>
                  {l}
                </text>
              ))}
            </g>
          </svg>
          <div className="mt-2">
            <div className="flex items-center justify-between py-1.5 border-b border-dashed border-white/10">
              <span className="text-xs text-muted-foreground">最大正相关</span>
              <span className="font-mono text-xs font-semibold tabular-nums">mom ↔ fun · 0.82</span>
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-xs text-muted-foreground">最大负相关</span>
              <span className="font-mono text-xs font-semibold tabular-nums">vol ↔ liq · -0.47</span>
            </div>
          </div>
        </Card>
      </div>

      {/* 玻璃卡片 grid（三等分）：IC 分布 + 策略分配 + 回撤带 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="p-4 glass">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <BarChart3 className="w-4 h-4 text-violet-300" />
              因子 IC 分布
            </div>
          </div>
          <svg viewBox="0 0 340 150" className="w-full h-auto">
            <g stroke="rgba(255,255,255,0.06)" strokeWidth="1">
              <line x1="0" y1="30" x2="340" y2="30" />
              <line x1="0" y1="75" x2="340" y2="75" />
              <line x1="0" y1="120" x2="340" y2="120" />
            </g>
            {IC_BARS.map((b, i) => (
              <rect key={i} x={b.x} y={b.y} width="18" height={b.h} rx="3" fill={b.fill} />
            ))}
            <line x1="0" y1="80" x2="340" y2="80" stroke="#22D3EE" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />
            <text x="8" y="145" fill="#6E7B98" fontSize="10" fontFamily="JetBrains Mono, monospace">
              IC=0
            </text>
          </svg>
        </Card>

        <Card className="p-4 glass">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <PieChart className="w-4 h-4 text-cyan-300" />
              策略分配
            </div>
          </div>
          <svg viewBox="0 0 340 150" className="w-full h-auto">
            <circle cx="120" cy="75" r="52" fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="14" />
            <circle
              cx="120"
              cy="75"
              r="52"
              fill="none"
              stroke="url(#chartsGradCyanViolet)"
              strokeWidth="14"
              strokeDasharray="190 327"
              transform="rotate(-90 120 75)"
              strokeLinecap="round"
            />
            <circle
              cx="120"
              cy="75"
              r="52"
              fill="none"
              stroke="#34D399"
              strokeWidth="14"
              strokeDasharray="82 327"
              strokeDashoffset="-190"
              transform="rotate(-90 120 75)"
              strokeLinecap="round"
            />
            <circle
              cx="120"
              cy="75"
              r="52"
              fill="none"
              stroke="#FBBF24"
              strokeWidth="14"
              strokeDasharray="41 327"
              strokeDashoffset="-272"
              transform="rotate(-90 120 75)"
              strokeLinecap="round"
            />
            <text x="104" y="72" fill="#EAF0FA" fontSize="16" fontWeight="700" fontFamily="JetBrains Mono, monospace">
              58%
            </text>
            <text x="98" y="88" fill="#6E7B98" fontSize="10">
              短线
            </text>
            <g fontSize="11">
              <rect x="230" y="40" width="10" height="10" rx="3" fill="url(#chartsGradCyanViolet)" />
              <text x="246" y="49" fill="#C9D4E8">短线 58%</text>
              <rect x="230" y="62" width="10" height="10" rx="3" fill="#34D399" />
              <text x="246" y="71" fill="#C9D4E8">中线 25%</text>
              <rect x="230" y="84" width="10" height="10" rx="3" fill="#FBBF24" />
              <text x="246" y="93" fill="#C9D4E8">长线 12%</text>
              <rect x="230" y="106" width="10" height="10" rx="3" fill="rgba(255,255,255,0.2)" />
              <text x="246" y="115" fill="#C9D4E8">预留 5%</text>
            </g>
          </svg>
        </Card>

        <Card className="p-4 glass">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Activity className="w-4 h-4 text-cyan-300" />
              回撤带
            </div>
            <span className="text-xs font-mono text-muted-foreground">近 30 日</span>
          </div>
          <svg viewBox="0 0 340 150" className="w-full h-auto">
            <path
              d="M0 40 L28 38 L56 42 L84 34 L112 36 L140 30 L168 34 L196 28 L224 30 L252 26 L280 28 L308 22 L340 24"
              fill="none"
              stroke="#34D399"
              strokeWidth="1.5"
              opacity="0.8"
            />
            <path
              d="M0 40 L28 52 L56 48 L84 62 L112 70 L140 78 L168 74 L196 88 L224 84 L252 92 L280 96 L308 104 L340 100"
              fill="none"
              stroke="#FB7185"
              strokeWidth="1.5"
            />
            <path
              d="M0 40 L28 52 L56 48 L84 62 L112 70 L140 78 L168 74 L196 88 L224 84 L252 92 L280 96 L308 104 L340 100 L340 150 L0 150 Z"
              fill="rgba(251,113,133,0.08)"
            />
            <g fontSize="10" fill="#6E7B98" fontFamily="JetBrains Mono, monospace">
              <text x="8" y="32">0%</text>
              <text x="8" y="108">-6.2%</text>
            </g>
          </svg>
          <div className="mt-2">
            <div className="flex items-center justify-between py-1.5 border-b border-dashed border-white/10">
              <span className="text-xs text-muted-foreground">最大回撤</span>
              <span className="font-mono text-xs font-semibold tabular-nums text-loss">-6.2%</span>
            </div>
            <div className="flex items-center justify-between py-1.5">
              <span className="text-xs text-muted-foreground">恢复天数</span>
              <span className="font-mono text-xs font-semibold tabular-nums">11 天</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
