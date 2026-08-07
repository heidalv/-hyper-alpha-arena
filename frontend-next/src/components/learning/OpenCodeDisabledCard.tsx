/**
 * OpenCode 中枢禁用状态卡
 *
 * 后端 OPENCODE_ENABLED=false（.env）→ sidecar 4096 未启动 → L2/L3/L4
 * 三个 Hermes job 依赖断链；opencode 路由整体注释（main.py）不再提供任何
 * /api/opencode/* 端点。本卡为静态说明，不发起任何已禁用接口请求。
 */
"use client";

import { AlertTriangle, Cpu, PowerOff, Server } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { SectionCard } from "../operations/IlcUi";

export function OpenCodeDisabledCard() {
  return (
    <div className="space-y-4">
      <SectionCard
        title="OpenCode 中枢"
        description="后端已禁用（OPENCODE_ENABLED=false）——不请求任何已注释路由，如实展示停摆原因"
        action={null}
      >
        <div className="rounded-lg border border-loss/30 bg-loss/5 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <PowerOff className="w-4 h-4 text-loss" />
            <span className="text-sm font-medium text-loss">已禁用（OPENCODE_ENABLED=false）</span>
            <Badge variant="destructive" className="text-[10px]">Sidecar 4096 未启动</Badge>
          </div>

          <ul className="text-xs text-muted-foreground space-y-1.5 pl-1">
            <li className="flex items-start gap-1.5">
              <Server className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>
                opencode sidecar（:4096）未运行——L2 提示词优化 / L3 架构演化 / L4 创生三个 Hermes job
                自 7-29 起每周期空转超时（WinError 10061 / timed out），已断链 8 天
              </span>
            </li>
            <li className="flex items-start gap-1.5">
              <Cpu className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>
                opencode 路由代码已在后端整体注释（main.py），/api/opencode/* 全部 404；本页不再发起这些请求
              </span>
            </li>
            <li className="flex items-start gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>
                L1 智慧提取（纯 SQL）不受影响，正常运转（648 次，今日 ok）；若要恢复 L2/L3/L4，
                需在 .env 置 OPENCODE_ENABLED=true 并启动 sidecar（后端整改项，不在本次前端范围内）
              </span>
            </li>
          </ul>
        </div>
      </SectionCard>
    </div>
  );
}

export default OpenCodeDisabledCard;
