/**
 * WizardContainer — step router + shared state for the 6-step strategy wizard.
 *
 * Replaces the monolithic AiStrategyWizard.tsx (2121 lines).
 * Each step is a focused component in wizard/Step{N}*.tsx.
 */
import React, { useState, useCallback, useEffect } from "react";
import { Button } from "@/app/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/app/components/ui/card";
import { Progress } from "@/app/components/ui/progress";
import { useWizardDraft } from "@/app/hooks/useWizardDraft";

import type { WizardData, StepDefinition } from "./types";
import { DEFAULT_WIZARD_DATA } from "./types";
import { Step1Requirement } from "./Step1Requirement";
import { Step2Framework } from "./Step2Framework";
import { Step3Signals, Step4Pool, Step5Risk, Step6Preview } from "./StepStubs";

// ── Step Registry ──────────────────────────────────────────────

const STEPS: StepDefinition[] = [
  { id: 1, title: "需求配置", description: "策略名称、交易对、周期", canProceed: (d) => !!d.name && !!d.accountId },
  { id: 2, title: "AI 框架生成", description: "LLM 生成策略逻辑框架", canProceed: (d) => !!d.generatedFramework },
  { id: 3, title: "信号设计", description: "LLM 生成交易信号", canProceed: (d) => !!d.generatedSignals },
  { id: 4, title: "因子池", description: "选择因子并配置权重", canProceed: () => true },
  { id: 5, title: "风控 & 仓位", description: "杠杆、止损、仓位配置", canProceed: () => true },
  { id: 6, title: "预览 & 创建", description: "检查并提交", canProceed: () => true },
];

// ── Props ──────────────────────────────────────────────────────

interface WizardContainerProps {
  /** Called when wizard wants to close (e.g. after successful creation) */
  onClose: () => void;
  /** Called after strategy is successfully created */
  onCreated?: (strategyId: string) => void;
}

// ── Component ──────────────────────────────────────────────────

export const WizardContainer: React.FC<WizardContainerProps> = ({ onClose, onCreated }) => {
  const [step, setStep] = useState(1);
  const [data, setData] = useState<WizardData>(DEFAULT_WIZARD_DATA);
  const [submitting, setSubmitting] = useState(false);
  const [generating, setGenerating] = useState(false);

  // ── Draft persistence ──
  const { restoreDraft, saveDraft, clearDraft, hasDraft } = useWizardDraft("ai-strategy-wizard");

  // Restore draft on mount
  useEffect(() => {
    if (hasDraft) {
      const saved = restoreDraft();
      if (saved) {
        setData(saved.data ?? DEFAULT_WIZARD_DATA);
        setStep(saved.step ?? 1);
      }
    }
  }, []);

  // Auto-save on data change
  useEffect(() => {
    if (step > 1 || data.name) {
      saveDraft({ data, step });
    }
  }, [data, step]);

  // ── Partial update helper ──
  const updateData = useCallback((partial: Partial<WizardData>) => {
    setData((prev) => ({ ...prev, ...partial }));
  }, []);

  // ── Step navigation ──
  const currentStep = STEPS.find((s) => s.id === step) ?? STEPS[0];
  const isLastStep = step === STEPS.length;
  const canNext = currentStep.canProceed(data) && !submitting && !generating;

  const handleNext = () => {
    if (isLastStep) return;
    if (!canNext) return;
    setStep((s) => Math.min(s + 1, STEPS.length));
  };

  const handlePrev = () => {
    setStep((s) => Math.max(s - 1, 1));
  };

  // ── Final submit ──
  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const resp = await fetch("/api/ai-strategies/create-complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (resp.ok) {
        const result = await resp.json();
        clearDraft();
        onCreated?.(result.strategy_id ?? result.id);
        onClose();
      }
    } finally {
      setSubmitting(false);
    }
  };

  // ── Step renderer ──
  const renderStep = () => {
    const props = { data, updateData, submitting, setSubmitting, generating, setGenerating };
    switch (step) {
      case 1: return <Step1Requirement {...props} />;
      case 2: return <Step2Framework {...props} />;
      case 3: return <Step3Signals {...props} />;
      case 4: return <Step4Pool {...props} />;
      case 5: return <Step5Risk {...props} />;
      case 6: return <Step6Preview {...props} />;
      default: return null;
    }
  };

  // ── Render ──
  return (
    <Card className="w-full max-w-3xl mx-auto">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>创建 AI 策略 — 步骤 {step}/{STEPS.length}</span>
          <Button variant="ghost" size="sm" onClick={onClose}>
            ✕
          </Button>
        </CardTitle>
        <Progress value={(step / STEPS.length) * 100} className="h-2 mt-2" />
        <p className="text-sm text-muted-foreground mt-1">
          {currentStep.title}: {currentStep.description}
        </p>
      </CardHeader>
      <CardContent className="min-h-[400px]">
        {renderStep()}
      </CardContent>
      <div className="flex justify-between p-4 border-t">
        <Button variant="outline" onClick={handlePrev} disabled={step === 1}>
          上一步
        </Button>
        {isLastStep ? (
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "创建中..." : "创建策略"}
          </Button>
        ) : (
          <Button onClick={handleNext} disabled={!canNext}>
            下一步
          </Button>
        )}
      </div>
    </Card>
  );
};

export default WizardContainer;
