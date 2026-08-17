"use client";

/**
 * 算力中心共享基件（第十章）
 *  - usePolling   轮询 hook（首次立即拉取 + setInterval）
 *  - ComputePanel 卡片面板（标题/说明/右上角 action/状态徽章）
 *  - StatusBadge  状态徽章：运行中(蓝)/空闲(绿)/异常(红)/停止(灰)/降级(黄)
 *  - GaugeRing    SVG 环形仪表（阈值告警色）
 *  - ProgressBar  进度条
 *  - fmt*         格式化工具
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RefreshCw, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// ───────────────────────────── 轮询 hook ─────────────────────────────

export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = []
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });

  const refresh = useCallback(() => {
    setLoading(true);
    fnRef
      .current()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => {
        // AbortError（请求超时/页面卸载）属瞬时现象，轮询下一轮自动恢复，不展示
        const name = e instanceof Error ? e.name : "";
        if (name !== "AbortError") {
          setError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => setLoading(false));
  }, []);

  // deps 变化（如时间范围切换）→ 立即重拉；轮询周期不变
  const depsKey = deps
    .map((d) => (typeof d === "object" ? JSON.stringify(d) : String(d)))
    .join("|");

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, intervalMs, depsKey]);

  return { data, loading, error, refresh, setData };
}

// ───────────────────────────── 状态徽章 ─────────────────────────────

export const BADGE_MAP: Record<
  string,
  { label: string; cls: string }
> = {
  running: { label: "运行中", cls: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30" },
  ok: { label: "正常", cls: "bg-profit/15 text-green-600 dark:text-profit border-green-500/30" },
  idle: { label: "空闲", cls: "bg-profit/15 text-green-600 dark:text-profit border-green-500/30" },
  error: { label: "异常", cls: "bg-loss/15 text-red-600 dark:text-red-400 border-red-500/30" },
  stopped: { label: "停止", cls: "bg-muted text-muted-foreground border-border" },
  degraded: { label: "降级", cls: "bg-warning/15 text-amber-600 dark:text-amber-400 border-amber-500/30" },
  disabled: { label: "禁用", cls: "bg-muted text-muted-foreground border-border" },
  off: { label: "已下线", cls: "bg-muted text-muted-foreground border-border" },
  ready: { label: "可用", cls: "bg-profit/15 text-green-600 dark:text-profit border-green-500/30" },
  placeholder: { label: "占位", cls: "bg-warning/15 text-amber-600 dark:text-amber-400 border-amber-500/30" },
};

/** status 由后端字段驱动，未知值一律灰色兜底，禁止前端臆造 */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  const key = (status || "").toLowerCase();
  const m = BADGE_MAP[key];
  if (!m) {
    return (
      <Badge variant="outline" className="font-normal">
        {status || "未知"}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className={cn("font-normal", m.cls)}>
      {m.label}
    </Badge>
  );
}

// ───────────────────────────── 卡片面板 ─────────────────────────────

export function ComputePanel({
  title,
  description,
  action,
  status,
  children,
  className,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  status?: string | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("h-full", className)}>
      {(title || description || action || status) && (
        <CardHeader className="flex flex-row items-start justify-between space-y-0 gap-3 pb-3">
          <div className="min-w-0 space-y-1">
            {title && <CardTitle className="text-base leading-none">{title}</CardTitle>}
            {description && (
              <CardDescription className="text-xs leading-relaxed">{description}</CardDescription>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {status && <StatusBadge status={status} />}
            {action}
          </div>
        </CardHeader>
      )}
      <CardContent className={title || description ? "pt-0" : "pt-6"}>{children}</CardContent>
    </Card>
  );
}

/** 卡内轻量分区：一层边框，禁止再套 ComputePanel */
export function SubSection({
  title,
  icon,
  action,
  badge,
  children,
  className,
}: {
  title: string;
  icon?: ReactNode;
  action?: ReactNode;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border/70 bg-muted/20 px-3 py-2.5 space-y-2",
        className
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-medium min-w-0">
          {icon}
          <span className="truncate">{title}</span>
          {badge}
        </div>
        {action ? <div className="flex items-center gap-2 flex-shrink-0">{action}</div> : null}
      </div>
      {children}
    </div>
  );
}

/** 页面分区小标题（Tab 内） */
export function SectionLabel({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline gap-2 px-0.5">
      <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

export function RefreshButton({
  onClick,
  loading,
  label = "刷新",
}: {
  onClick: () => void;
  loading?: boolean;
  label?: string;
}) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={loading}>
      <RefreshCw className={cn("h-3.5 w-3.5 mr-1.5", loading && "animate-spin")} />
      {label}
    </Button>
  );
}

export function LoadingBox({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="flex items-center justify-center py-8 text-muted-foreground text-sm gap-2">
      <Loader2 className="w-4 h-4 animate-spin" />
      {text}
    </div>
  );
}

export function EmptyBox({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

// ───────────────────────────── 环形仪表 ─────────────────────────────

export function GaugeRing({
  value,
  max = 100,
  label,
  unit = "%",
  threshold,
  reverse,
}: {
  value: number | null | undefined;
  max?: number;
  label: string;
  unit?: string;
  /** 超过该值告警红 */
  threshold?: number;
  /** 反向阈值（低于告警，如显存） */
  reverse?: boolean;
}) {
  const v = typeof value === "number" && isFinite(value) ? value : 0;
  const ratio = Math.min(1, Math.max(0, v / max));
  const danger = threshold != null && (reverse ? v < threshold : v > threshold);
  const color = danger ? "#ef4444" : ratio > 0.8 ? "#f59e0b" : "#22c55e";
  const r = 26;
  const c = 2 * Math.PI * r;
  const id = `gauge-${label.replace(/\s/g, "")}`;

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-16 h-16">
        <svg viewBox="0 0 64 64" className="w-16 h-16 -rotate-90">
          <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(148,163,184,0.2)" strokeWidth="6" />
          <circle
            cx="32"
            cy="32"
            r={r}
            fill="none"
            stroke={color}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${c}`}
            strokeDashoffset={c * (1 - ratio)}
            style={{ transition: "stroke-dashoffset 0.5s ease, stroke 0.3s" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xs font-bold tabular-nums" style={{ color }}>
            {typeof value === "number" ? Math.round(v) : "—"}
            {unit}
          </span>
        </div>
      </div>
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </div>
  );
}

// ───────────────────────────── 进度条 ─────────────────────────────

export function ProgressBar({
  percent,
  tone,
}: {
  percent: number;
  tone?: "ok" | "warn" | "bad";
}) {
  const p = Math.min(100, Math.max(0, percent));
  const color =
    tone === "bad" ? "bg-loss" : tone === "warn" ? "bg-warning" : "bg-profit";
  return (
    <div className="w-full h-2 rounded-full bg-muted overflow-hidden">
      <div
        className={cn("h-full rounded-full transition-all duration-500", color)}
        style={{ width: `${p}%` }}
      />
    </div>
  );
}

// ───────────────────────────── 格式化工具 ─────────────────────────────

export function fmtNum(n: number | null | undefined, digits = 1): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
  return n.toFixed(digits);
}

export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts || !isFinite(ts)) return "—";
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function fmtDt(iso: string | number | null | undefined): string {
  if (iso == null || iso === "") return "—";
  // 秒级时间戳（数字或数字字符串）
  if (typeof iso === "number" || /^\d{9,}$/.test(String(iso))) {
    return fmtTime(Number(iso));
  }
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 面板内容区统一错误提示 */
export function PanelError({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="text-xs text-red-500 mb-3">{error}</p>;
}
