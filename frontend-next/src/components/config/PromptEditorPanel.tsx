"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Save, Loader2, Lock, CheckCircle2, AlertTriangle, Play } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 中长线提示词编辑器（已并入长线配置页）。
 * 默认 long；中线已合并进长线，仍保留 mid 数据源以便历史任务可编辑。
 */
export function PromptEditorPanel({
  defaultTier = "long",
}: {
  defaultTier?: "mid" | "long";
}) {
  const [tier, setTier] = useState<"mid" | "long">(defaultTier);
  const [prompts, setPrompts] = useState<Record<string, any>>({});
  const [activeTask, setActiveTask] = useState("");
  const [editSystem, setEditSystem] = useState("");
  const [editTask, setEditTask] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getPrompts(tier);
      const data = resp?.prompts ?? resp ?? {};
      setPrompts(data);
      const keys = Object.keys(data);
      const first = keys.includes(activeTask) ? activeTask : keys[0];
      if (first) {
        setActiveTask(first);
        setEditSystem(data[first].system_prompt || "");
        setEditTask(data[first].task_prompt || "");
      } else {
        setActiveTask("");
        setEditSystem("");
        setEditTask("");
      }
      setDirty(false);
      setTestResult(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [tier]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    void load();
  }, [load]);

  const switchTask = (taskId: string) => {
    const p = prompts[taskId];
    if (!p) return;
    setActiveTask(taskId);
    setEditSystem(p.system_prompt || "");
    setEditTask(p.task_prompt || "");
    setDirty(false);
    setTestResult(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.updatePrompt(tier, {
        task_id: activeTask,
        system_prompt: editSystem,
        task_prompt: editTask,
      });
      setDirty(false);
      setPrompts((prev) => ({
        ...prev,
        [activeTask]: {
          ...prev[activeTask],
          system_prompt: editSystem,
          task_prompt: editTask,
          is_overridden: true,
        },
      }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testPrompt(tier, {
        task_id: activeTask,
        system_prompt: editSystem,
        task_prompt: editTask,
      });
      setTestResult(result);
    } catch (e: any) {
      setTestResult({ success: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const current = prompts[activeTask];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-xs text-muted-foreground">
          编辑中长线 Agent 系统角色与任务指令（原「提示词管理」）
        </p>
        <div className="flex gap-1">
          <button
            onClick={() => setTier("long")}
            className={cn(
              "px-3 py-1 text-xs rounded",
              tier === "long" ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground"
            )}
          >
            长线
          </button>
          <button
            onClick={() => setTier("mid")}
            className={cn(
              "px-3 py-1 text-xs rounded",
              tier === "mid" ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground"
            )}
            title="中线已因子化：LLM 仅保留边缘裁决（fail-closed）；此页仅供过渡期/历史提示词编辑"
          >
            中线(旧)
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-loss bg-loss/10 p-2 rounded">⚠️ {error}</div>}

      {Object.keys(prompts).length > 1 && (
        <div className="flex gap-2 flex-wrap">
          {Object.entries(prompts).map(([id, p]: [string, any]) => (
            <Button
              key={id}
              variant={activeTask === id ? "default" : "outline"}
              size="sm"
              onClick={() => switchTask(id)}
            >
              {p.label}
              {p.is_overridden && <span className="ml-1 text-xs">●</span>}
            </Button>
          ))}
        </div>
      )}

      {!current && (
        <div className="text-sm text-muted-foreground py-8 text-center">暂无提示词任务</div>
      )}

      {current && (
        <>
          <Card className="p-4">
            <div className="text-sm font-medium mb-2">系统角色</div>
            <textarea
              value={editSystem}
              onChange={(e) => {
                setEditSystem(e.target.value);
                setDirty(true);
              }}
              className="w-full min-h-[100px] p-3 text-sm rounded border border-border bg-background font-mono resize-y"
            />
          </Card>

          <Card className="p-4">
            <div className="text-sm font-medium mb-2">任务指令</div>
            <textarea
              value={editTask}
              onChange={(e) => {
                setEditTask(e.target.value);
                setDirty(true);
              }}
              className="w-full min-h-[420px] p-3 text-sm rounded border border-border bg-background font-mono resize-y leading-relaxed"
            />
          </Card>

          {current.schema && (
            <Card className="border-primary/20 p-4">
              <div className="text-sm font-medium mb-2 flex items-center gap-1">
                <Lock className="w-3.5 h-3.5 text-primary" />
                JSON 输出契约（只读）
              </div>
              <div className="space-y-1">
                {Object.entries(current.schema).map(([field, schema]: [string, any]) => (
                  <div key={field} className="flex items-center gap-2 text-xs font-mono">
                    <span
                      className={cn(
                        "px-1 rounded",
                        schema.required ? "bg-loss/10 text-loss" : "bg-muted text-muted-foreground"
                      )}
                    >
                      {schema.required ? "必填" : "可选"}
                    </span>
                    <span className="font-bold w-32 truncate">&quot;{field}&quot;</span>
                    <span className="text-muted-foreground">{schema.type}</span>
                    {schema.enum && (
                      <span className="text-primary">[{schema.enum.join("/")}]</span>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          )}

          <div className="flex gap-2 flex-wrap items-center">
            <Button variant="outline" size="sm" onClick={handleTest} disabled={testing || dirty}>
              {testing ? (
                <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5 mr-1" />
              )}
              测试
            </Button>
            <Button size="sm" onClick={handleSave} disabled={!dirty || saving}>
              {saving ? (
                <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5 mr-1" />
              )}
              保存提示词
            </Button>
            {dirty && (
              <Badge variant="destructive" className="text-xs">
                未保存
              </Badge>
            )}
          </div>

          {testResult && (
            <Card
              className={
                testResult.success && testResult.all_fields_present
                  ? "border-profit/30"
                  : "border-loss/30"
              }
            >
              <div className="p-4">
                <div className="flex items-center gap-2 mb-2">
                  {testResult.success && testResult.all_fields_present ? (
                    <>
                      <CheckCircle2 className="w-4 h-4 text-profit" />
                      <span className="text-sm text-profit">测试通过</span>
                    </>
                  ) : (
                    <>
                      <AlertTriangle className="w-4 h-4 text-loss" />
                      <span className="text-sm text-loss">测试未通过</span>
                    </>
                  )}
                </div>
                {testResult.error && (
                  <div className="text-xs text-loss mb-2">{testResult.error}</div>
                )}
                {testResult.parsed && (
                  <pre className="text-xs bg-muted/50 p-2 rounded overflow-auto max-h-60 font-mono">
                    {JSON.stringify(testResult.parsed, null, 2)}
                  </pre>
                )}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
