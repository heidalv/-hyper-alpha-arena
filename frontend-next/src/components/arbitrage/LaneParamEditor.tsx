"use client";

/**
 * LaneParamEditor — 车道参数编辑器（白名单 + 范围校验 + 恢复默认）
 *
 * 设计 §3.6：车道报价/风控参数（w 下限、波动/库存系数、单边持仓、净敞口等）。
 * 数据来源 `GET /api/trading/config/lanes/{id}`：`params` 为报价参数（QuoteParams），
 * `limits` 为风控参数（LaneRiskLimits），`editable_keys` 为后端白名单（10 个）。
 * 可编辑键的值需从 `params`/`limits` 中合并取出，保存时统一放到 `params` 提交。
 *
 * 约束：
 *  - 走 `confirmDialog`（保存前确认）；
 *  - 客户端先做范围校验（_bp ≥ 0；_ratio ∈ (0,1]；秒/sigma 为正），与后端一致。
 */
import { useMemo, useState } from "react";
import { Save, RotateCcw, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtNum } from "@/lib/format";
import { confirmDialog } from "@/lib/confirm";
import { toast } from "@/lib/toast";
import type { LaneConfig } from "@/lib/trading-api";

/** 引擎代码默认值（QuoteParams / LaneRiskLimits 构造默认，未做 F59 调参前） */
const DEFAULT_PARAMS: Record<string, number> = {
  w_base_bp: 5.0,
  min_width_bp: 3.0,
  min_width_reduce_bp: 1.0,
  max_width_bp: 60.0,
  k_vol: 0.5,
  k_inv: 0.3,
  max_one_side_seconds: 300.0,
  vol_pause_sigma: 1.5,
  max_net_directional_ratio: 0.10,
  max_net_exposure_ratio: 0.30,
};

/** 每个可编辑键的中文标签 + 单位 + 范围提示 */
const KEY_META: Record<string, { label: string; unit: string; range: string; min: number; max: number }> = {
  w_base_bp: { label: "基础挂单距离", unit: "bp", range: "≥ 0", min: 0, max: 1000 },
  min_width_bp: { label: "最小加仓宽度", unit: "bp", range: "≥ 0", min: 0, max: 100 },
  min_width_reduce_bp: { label: "减仓侧最小宽度", unit: "bp", range: "≥ 0", min: 0, max: 100 },
  max_width_bp: { label: "最大挂单宽度", unit: "bp", range: "≥ 0", min: 0, max: 1000 },
  k_vol: { label: "波动放大系数", unit: "", range: "(0,1]", min: 0.0001, max: 1 },
  k_inv: { label: "库存偏斜系数", unit: "", range: "(0,1]", min: 0.0001, max: 1 },
  max_one_side_seconds: { label: "单边持仓超时", unit: "秒", range: "> 0", min: 1, max: 86400 },
  vol_pause_sigma: { label: "波动暂停阈值", unit: "σ", range: "> 0", min: 0.0001, max: 20 },
  max_net_directional_ratio: { label: "单边方向敞口上限", unit: "", range: "(0,1]", min: 0.0001, max: 1 },
  max_net_exposure_ratio: { label: "总净敞口上限", unit: "", range: "(0,1]", min: 0.0001, max: 1 },
};

function resolveValue(config: LaneConfig | null | undefined, key: string): number {
  if (!config) return DEFAULT_PARAMS[key] ?? 0;
  if (key in config.params) return config.params[key];
  if (key in config.limits) return config.limits[key];
  return DEFAULT_PARAMS[key] ?? 0;
}

const isEmpty = (v: string) => v.trim() === "";

export function LaneParamEditor({
  config,
  busy,
  onSave,
  className,
}: {
  config?: LaneConfig | null;
  busy?: boolean;
  onSave?: (params: Record<string, number>) => Promise<unknown> | void;
  className?: string;
}) {
  const editableKeys = config?.editable_keys ?? Object.keys(DEFAULT_PARAMS);
  // 只记录用户「改动/恢复默认」后的覆盖值；未改动的字段直接取 config 解析值。
  // 车牌切换用 `key={lane_id}` 由父级强制重挂载，因此这里的初始值总对应当前 config。
  const [pending, setPending] = useState<Record<string, string>>({});

  const display = (key: string): string => {
    if (key in pending) return pending[key];
    const v = resolveValue(config, key);
    return Number.isFinite(v) ? String(v) : "";
  };
  const isEdited = (key: string): boolean => key in pending;

  const setValue = (key: string, v: string) => {
    setPending((prev) => ({ ...prev, [key]: v }));
  };

  const resetDefaults = () => {
    const next: Record<string, string> = {};
    for (const k of editableKeys) next[k] = String(DEFAULT_PARAMS[k] ?? "");
    setPending(next);
  };

  /** 校验单键，返回错误文案或 null */
  const validate = (key: string, raw: string | undefined): string | null => {
    const meta = KEY_META[key];
    if (isEmpty(raw ?? "")) return "必填";
    const n = Number(raw);
    if (!Number.isFinite(n)) return "须为数字";
    if (!meta) return null;
    if (n < meta.min || n > meta.max) return `范围 ${meta.range}`;
    if (key.endsWith("_ratio") && !(n > 0 && n <= 1)) return "范围 (0,1]";
    return null;
  };

  const errors = useMemo(() => {
    const e: Record<string, string | null> = {};
    for (const k of editableKeys) e[k] = validate(k, display(k));
    return e;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, editableKeys]);

  const hasError = Object.values(errors).some(Boolean);

  const onSaveRequest = async () => {
    if (hasError) {
      toast.error("存在校验错误，请修正后再保存");
      return;
    }
    if (!onSave) return;
    const changed: Record<string, number> = {};
    for (const k of editableKeys) {
      const cur = resolveValue(config, k);
      const next = Number(display(k));
      if (isEdited(k) && Number.isFinite(next) && Math.abs(next - cur) > 1e-12) {
        changed[k] = next;
      }
    }
    if (Object.keys(changed).length === 0) {
      toast.info("没有需要保存的改动");
      return;
    }
    const ok = await confirmDialog({
      title: `保存车道参数「${config?.lane_id ?? ""}」？`,
      description: `将更新 ${Object.keys(changed).length} 个参数：${Object.keys(changed).join("、")}（范围校验已通过）。`,
      tone: "primary",
      confirmText: "保存",
      cancelText: "取消",
    });
    if (!ok) return;
    try {
      await onSave(changed);
      setPending({});
      toast.success("车道参数已保存");
    } catch (e) {
      toast.error(`保存失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          白名单：{editableKeys.join("、")}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={resetDefaults}
            className="inline-flex items-center gap-1 rounded-md border border-border/40 px-2 py-1 text-[11px] text-muted-foreground hover:border-cyan-400/30 hover:text-cyan-300"
          >
            <RotateCcw className="h-3 w-3" /> 恢复默认
          </button>
          <button
            type="button"
            onClick={onSaveRequest}
            disabled={busy || hasError || !onSave}
            className="inline-flex items-center gap-1 rounded-md border border-profit/30 bg-profit/10 px-2.5 py-1 text-[11px] text-profit hover:bg-profit/20 disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} 保存
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {editableKeys.map((key) => {
          const meta = KEY_META[key];
          const raw = display(key);
          const err = errors[key];
          const isDirty = isEdited(key);
          const defaultValue = resolveValue(config, key);
          return (
            <label key={key} className="block">
              <span className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
                <span>{meta?.label ?? key}</span>
                <span className="font-mono tabular-nums">
                  {isDirty ? "（修改中）" : `当前 ${fmtNum(defaultValue, meta?.unit ? 2 : 4)}${meta?.unit ? ` ${meta.unit}` : ""}`}
                </span>
              </span>
              <input
                type="number"
                step="any"
                value={raw ?? ""}
                onChange={(e) => setValue(key, e.target.value)}
                className={cn(
                  "w-full rounded-md border bg-muted/20 px-2.5 py-1.5 font-mono text-xs tabular-nums outline-none transition-colors focus:border-cyan-400/40",
                  err ? "border-loss/50" : "border-border/40"
                )}
              />
              <span className={cn("mt-0.5 block text-[11px]", err ? "text-loss" : "text-muted-foreground")}>
                {err ?? `范围 ${meta?.range ?? "—"}${meta?.unit ? ` · 单位 ${meta.unit}` : ""}`}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
