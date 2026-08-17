"use client";

/**
 * 开源框架集成卡 — 紧凑网格排版
 */
import { CheckCircle2, XCircle, Scale, Boxes } from "lucide-react";
import { ComputePanel } from "./common";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type State = "integrated" | "not" | "tradeoff";

interface OSSItem {
  name: string;
  state: State;
  note: string;
}

const ITEMS: OSSItem[] = [
  { name: "joblib", state: "integrated", note: "GP / MCTS 并行评估" },
  { name: "Qlib", state: "not", note: "自有 WFO/DSR/PBO 替代" },
  { name: "FinRL", state: "not", note: "DRL 已整体下线" },
  { name: "FreqAI", state: "tradeoff", note: "仅对标，不引入" },
  { name: "vectorbt", state: "not", note: "无引入" },
  { name: "PyPortfolioOpt", state: "not", note: "无引入" },
  { name: "Ray", state: "tradeoff", note: "Windows 优先 joblib" },
  { name: "实盘链路红线", state: "integrated", note: "产出走既有晋升链路" },
];

const STATE_META: Record<
  State,
  { label: string; cls: string; icon: React.ComponentType<{ className?: string }> }
> = {
  integrated: {
    label: "已集成",
    cls: "border-green-500/40 bg-profit/10 text-green-600 dark:text-profit",
    icon: CheckCircle2,
  },
  not: {
    label: "未集成",
    cls: "border-border bg-muted text-muted-foreground",
    icon: XCircle,
  },
  tradeoff: {
    label: "符合取舍",
    cls: "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400",
    icon: Scale,
  },
};

export function OSSIntegrationCard() {
  const integrated = ITEMS.filter((i) => i.state === "integrated").length;
  const not = ITEMS.filter((i) => i.state === "not").length;
  const tradeoff = ITEMS.filter((i) => i.state === "tradeoff").length;

  return (
    <ComputePanel
      title="开源框架集成"
      description="实机审计结论，静态展示"
      action={
        <Badge variant="outline" className="font-normal">
          <Boxes className="w-3 h-3 mr-1" />
          {integrated} 集成 / {not} 未集成 / {tradeoff} 取舍
        </Badge>
      }
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {ITEMS.map((it) => {
          const meta = STATE_META[it.state];
          const Icon = meta.icon;
          return (
            <div
              key={it.name}
              className="flex items-start justify-between gap-2 rounded-lg border border-border/70 bg-muted/15 px-3 py-2.5"
            >
              <div className="min-w-0 space-y-0.5">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <Icon
                    className={cn(
                      "w-3.5 h-3.5 flex-shrink-0",
                      it.state === "integrated" && "text-profit"
                    )}
                  />
                  {it.name}
                </div>
                <p className="text-[11px] text-muted-foreground pl-5">{it.note}</p>
              </div>
              <Badge variant="outline" className={cn("font-normal whitespace-nowrap", meta.cls)}>
                {meta.label}
              </Badge>
            </div>
          );
        })}
      </div>
    </ComputePanel>
  );
}
