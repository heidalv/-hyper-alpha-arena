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
          title="周期报告（日报 / 周报）"
          subtitle="短线 / 中线 / 长线 三周期 · 含亏损归因 · 日报每日 08:05、周报每周一 08:30 后台生成"
          breadcrumb={[{ label: "市场 & 分析" }, { label: "周期报告" }]}
        />
        <LongReportsPanel />
      </div>
    </Suspense>
  );
}