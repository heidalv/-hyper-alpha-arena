/**
 * Step 2-6 stub components for the Wizard.
 * Each delegates to the original monolithic AiStrategyWizard logic during migration.
 * TODO: Extract specific step logic from AiStrategyWizard.tsx into each file.
 */
import React from "react";
import type { StepProps } from "./types";

export const Step2Framework: React.FC<StepProps> = ({ data: _data, updateData: _update, generating: _gen, setGenerating: _setGen }) => (
  <div className="space-y-3 text-sm">
    <div className="p-3 bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 rounded">
      <p className="font-medium text-blue-800 dark:text-blue-200">AI 框架生成</p>
      <p className="text-muted-foreground mt-1">系统将基于您选择的策略类型自动生成 AI 决策框架，包括分析周期、信号源和风控参数。</p>
    </div>
    <p className="text-xs text-muted-foreground">使用: POST /api/ai-strategies/generate-framework (120s timeout)</p>
  </div>
);

export const Step3Signals: React.FC<StepProps> = () => (
  <div className="space-y-3 text-sm">
    <div className="p-3 bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 rounded">
      <p className="font-medium text-amber-800 dark:text-amber-200">信号生成配置</p>
      <p className="text-muted-foreground mt-1">配置 AI 信号源：技术指标信号、市场情绪信号、链上数据信号的组合权重与触发阈值。</p>
    </div>
  </div>
);

export const Step4Pool: React.FC<StepProps> = () => (
  <div className="space-y-3 text-sm">
    <div className="p-3 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded">
      <p className="font-medium text-green-800 dark:text-green-200">因子池配置</p>
      <p className="text-muted-foreground mt-1">选择并配置 AI 因子：趋势因子、波动率因子、动量因子、成交量因子的启用/禁用与权重分配。</p>
    </div>
  </div>
);

export const Step5Risk: React.FC<StepProps> = () => (
  <div className="space-y-3 text-sm">
    <div className="p-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded">
      <p className="font-medium text-red-800 dark:text-red-200">风控配置</p>
      <p className="text-muted-foreground mt-1">设置风控参数：最大杠杆、止损比例、单次最大仓位、日最大亏损限额、连续亏损熔断阈值。</p>
    </div>
  </div>
);

export const Step6Preview: React.FC<StepProps> = ({ data }) => (
  <div className="space-y-3 text-sm">
    <p>Step 6 — 预览 & 确认创建</p>
    <pre className="text-xs bg-muted p-2 rounded max-h-64 overflow-auto">
      {JSON.stringify(data, null, 2)}
    </pre>
  </div>
);
