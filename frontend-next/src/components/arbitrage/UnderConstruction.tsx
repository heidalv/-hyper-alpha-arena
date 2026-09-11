"use client";

/**
 * UnderConstruction — 建设中占位（四态齐全，不空白）
 *
 * 本阶段仅交付「总览 + 车道」，其余视图先渲染可读的占位，说明本页将展示什么、
 * 及其后端数据来源。用 `DataState`(empty) 呈现，保证不是空白。
 */
import { Hammer } from "lucide-react";

export function UnderConstruction({
  title,
  desc,
  features,
}: {
  title: string;
  desc: string;
  features?: string[];
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-border/40 bg-card glass px-6 py-10 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-xl border border-cyan-400/25 bg-gradient-to-br from-cyan-400/15 to-violet-500/15 text-cyan-300">
        <Hammer className="h-5 w-5" />
      </span>
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">{desc}</p>
      </div>
      {features && features.length > 0 && (
        <ul className="flex flex-wrap justify-center gap-1.5">
          {features.map((f) => (
            <li key={f} className="rounded-full border border-border/40 bg-muted/20 px-2.5 py-0.5 text-[11px] text-muted-foreground">
              {f}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-1 text-[11px] text-muted-foreground">
        该视图将在下一阶段接入真实数据（当前为建设中占位）
      </div>
    </div>
  );
}
