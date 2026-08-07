/**
 * Step 2: AI Framework Generation — calls POST /api/ai-strategies/generate-framework
 *
 * Extracted from original AiStrategyWizard.tsx (LLM generation section).
 */
import React, { useState } from "react";
import { Button } from "@/app/components/ui/button";
import { Textarea } from "@/app/components/ui/textarea";
import { Label } from "@/app/components/ui/label";
import { Card, CardContent } from "@/app/components/ui/card";
import { toast } from "@/app/components/ui/use-toast";
import type { StepProps } from "./types";

export const Step2Framework: React.FC<StepProps> = ({
  data, updateData, generating, setGenerating,
}) => {
  const [rawResponse, setRawResponse] = useState<string>("");

  const handleGenerate = async () => {
    if (!data.name || !data.accountId) {
      toast({ title: "请先完成 Step 1 的必填项", variant: "destructive" });
      return;
    }

    setGenerating(true);
    const toastId = toast({ title: "AI 正在生成策略框架...", description: "预计 30-120 秒" });

    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 120_000);

      const resp = await fetch("/api/ai-strategies/generate-framework", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_input: data.frameworkPrompt || `为 ${data.primarySymbol} 设计一个 ${data.timeframe} 周期的交易策略`,
          strategy_type: data.tradingStyleId ? "custom" : undefined,
          market_context: {
            symbol: data.primarySymbol,
            timeframe: data.timeframe,
          },
        }),
        signal: controller.signal,
      });

      clearTimeout(timeout);

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      const result = await resp.json();
      if (result.success && result.framework) {
        updateData({
          generatedFramework: result.framework,
          generatedConfidence: result.confidence ?? 0.7,
        });
        setRawResponse(JSON.stringify(result.framework, null, 2));
        toast({ title: "框架生成成功", description: `置信度: ${((result.confidence ?? 0.7) * 100).toFixed(0)}%` });
      } else {
        throw new Error(result.error || "未知错误");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "生成失败";
      if ((err as Error)?.name === "AbortError") {
        toast({ title: "生成超时 (120s)", description: "请重试或简化需求", variant: "destructive" });
      } else {
        toast({ title: "生成失败", description: msg, variant: "destructive" });
      }
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Prompt input */}
      <div>
        <Label htmlFor="framework-prompt">策略需求描述</Label>
        <Textarea
          id="framework-prompt"
          placeholder={`描述你想要的策略逻辑，例如：

"设计一个 ${data.primarySymbol || 'BTC'} ${data.timeframe || '15m'} 的趋势跟随策略。
使用 EMA 交叉 + RSI 确认入场，
ATR 动态止损，
突破布林带上轨加仓..."`}
          value={data.frameworkPrompt}
          onChange={(e) => updateData({ frameworkPrompt: e.target.value })}
          rows={5}
          disabled={generating}
        />
      </div>

      {/* Generate button */}
      <Button
        onClick={handleGenerate}
        disabled={generating || !data.name}
        className="w-full"
      >
        {generating ? (
          <span className="flex items-center gap-2">
            <span className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />
            AI 思考中...
          </span>
        ) : (
          "🤖 生成策略框架"
        )}
      </Button>

      {/* Result preview */}
      {data.generatedFramework && (
        <Card>
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-green-600 mb-2">
              ✅ 框架已生成 (置信度: {((data.generatedConfidence ?? 0.7) * 100).toFixed(0)}%)
            </p>
            <pre className="text-xs bg-muted p-2 rounded max-h-48 overflow-auto whitespace-pre-wrap">
              {rawResponse}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default Step2Framework;
