"use client";

/**
 * 开源框架集成卡（第十章 10.3）
 * 8 项开关行三态：已集成(绿) / 未集成(灰) / 符合取舍(蓝)
 * 静态数据来源：第十章审计结论（实机核验 2026-08-06），无后端端点，不伪造。
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
  { name: "joblib", state: "integrated", note: "gp_miner / mcts_miner loky 真实使用（32 workers 并行）" },
  { name: "Qlib", state: "not", note: "无 import；研究层借自有管线（WFO/DSR/PBO）等效替代" },
  { name: "FinRL", state: "not", note: "无 import（DRL 已整体下线）" },
  { name: "FreqAI", state: "tradeoff", note: "仅对标注释，符合“不引入”取舍" },
  { name: "vectorbt", state: "not", note: "无 import" },
  { name: "PyPortfolioOpt", state: "not", note: "无 import" },
  { name: "Ray", state: "tradeoff", note: "无 import；符合“Windows 受限 joblib 优先”取舍" },
  { name: "红线：不开新实盘链路", state: "integrated", note: "产出全走 purge→lifecycle→shadow_judge→online_weights" },
];

const STATE_META: Record<
  State,
  { label: string; cls: string; icon: React.ComponentType<{ className?: string }> }
> = {
  integrated: {
    label: "已集成",
    cls: "border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400",
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
  return (
    <ComputePanel
      title="开源框架集成"
      description="第十章审计实机结论（2026-08-06 核验，静态展示）"
      action={
        <Badge variant="outline" className="font-normal">
          <Boxes className="w-3 h-3 mr-1" />
          {ITEMS.filter((i) => i.state === "integrated").length} 集成 /{" "}
          {ITEMS.filter((i) => i.state === "not").length} 未集成 /{" "}
          {ITEMS.filter((i) => i.state === "tradeoff").length} 取舍
        </Badge>
      }
    >
      <ul className="space-y-1.5">
        {ITEMS.map((it) => {
          const meta = STATE_META[it.state];
          const Icon = meta.icon;
          return (
            <li
              key={it.name}
              className="flex items-center justify-between gap-2 rounded-md border border-border px-2.5 py-2"
            >
              <span className="flex items-center gap-2 text-xs min-w-0">
                <Icon className={cn("w-3.5 h-3.5 flex-shrink-0", it.state === "integrated" && "text-green-500")} />
                <span className="font-medium whitespace-nowrap">{it.name}</span>
                <span className="text-[11px] text-muted-foreground truncate">{it.note}</span>
              </span>
              <Badge variant="outline" className={cn("font-normal whitespace-nowrap", meta.cls)}>
                {meta.label}
              </Badge>
            </li>
          );
        })}
      </ul>
      <p className="text-[10px] text-muted-foreground mt-2">
        取舍项：Qlib 借研究层 → 自有管线替代；Ray → Windows 受限 joblib 优先；FreqAI → 不引入。
      </p>
    </ComputePanel>
  );
}
