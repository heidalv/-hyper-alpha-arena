"use client";

import { Suspense } from "react";
import { Activity, Loader2 } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { LongReportsPanel } from "@/components/long/LongReportsPanel";

export default function ReportsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <div className="space-y-4">
        <PageHeader
          icon={<Activity className="w-4 h-4" />}
          title="周期报告"
          subtitle="两条车道各自成段 · 日内（中线槽位，主看 1h） / 长线趋势（主看 4h）· 含周期身份、持仓时长与亏损归因 · 日报每日 08:05、周报每周一 08:30 后台生成"
          breadcrumb={[{ label: "市场 & 分析" }, { label: "周期报告" }]}
        />
        <LongReportsPanel />
      </div>
    </Suspense>
  );
}