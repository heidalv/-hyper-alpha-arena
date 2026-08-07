/**
 * 提示词结构化管理页 — 中线/长线共用，通过 tier prop 区分
 *
 * 特点：
 * - 分区显示：✏️可编辑区（分析步骤/措辞）+ 🔒JSON输出契约（只读）
 * - 保存时后端校验 locked 字段完整
 * - 测试功能：调 LLM 验证输出格式
 * - 恢复默认
 */
import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { RefreshCw, Save, Loader2, Play, RotateCcw, Lock, AlertTriangle, CheckCircle2 } from 'lucide-react';
import {
  fetchPrompts, updatePrompt, resetPrompt, testPrompt,
  type PromptData, type TestResult, type Tier,
} from '@/lib/strategyPromptApi';

export default function StrategyPromptPage({ tier }: { tier: Tier }) {
  const title = tier === 'mid' ? '📝 中线提示词管理' : '📝 长线提示词管理';
  const [prompts, setPrompts] = useState<Record<string, PromptData>>({});
  const [activeTask, setActiveTask] = useState<string>('');
  const [editSystem, setEditSystem] = useState('');
  const [editTask, setEditTask] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [dirty, setDirty] = useState(false);

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchPrompts(tier);
      setPrompts(data);
      const firstTask = Object.keys(data)[0];
      if (firstTask) {
        setActiveTask(firstTask);
        setEditSystem(data[firstTask].system_prompt);
        setEditTask(data[firstTask].task_prompt);
      }
      setDirty(false);
      setTestResult(null);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, [tier]);

  useEffect(() => { setLoading(true); loadData(); }, [loadData]);

  const switchTask = (taskId: string) => {
    const p = prompts[taskId];
    if (!p) return;
    setActiveTask(taskId);
    setEditSystem(p.system_prompt);
    setEditTask(p.task_prompt);
    setDirty(false);
    setTestResult(null);
  };

  const handleSave = async () => {
    if (!activeTask) return;
    setSaving(true);
    setError(null);
    try {
      const resp = await updatePrompt(tier, activeTask, editSystem, editTask);
      if (resp.success) {
        setDirty(false);
        // 更新本地状态
        setPrompts(prev => ({
          ...prev,
          [activeTask]: { ...prev[activeTask], system_prompt: editSystem, task_prompt: editTask, is_overridden: true }
        }));
      } else {
        setError(resp.error || '保存失败（结构校验未通过）');
      }
    } catch (e: any) {
      setError(e.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!activeTask) return;
    try {
      await resetPrompt(tier, activeTask);
      await loadData();
    } catch (e: any) {
      setError(e.message || '恢复失败');
    }
  };

  const handleTest = async () => {
    if (!activeTask) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testPrompt(tier, activeTask, editSystem, editTask);
      setTestResult(result);
    } catch (e: any) {
      setTestResult({ success: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const currentPrompt = prompts[activeTask];

  if (loading) {
    return <div className="flex items-center justify-center h-40"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="flex flex-col h-full overflow-auto p-4 space-y-4 max-w-5xl mx-auto">
      {/* 标题 */}
      <div className="flex items-center justify-between flex-shrink-0">
        <h2 className="text-xl font-bold">{title}</h2>
        <div className="flex items-center gap-2">
          {dirty && <Badge variant="destructive" className="text-xs">未保存</Badge>}
          {currentPrompt?.is_overridden && <Badge variant="secondary" className="text-xs">已自定义</Badge>}
        </div>
      </div>

      {error && (
        <div className="text-sm text-red-500 bg-red-500/10 p-2 rounded flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" /> {error}
        </div>
      )}

      {/* Task 选择器 */}
      {Object.keys(prompts).length > 1 && (
        <div className="flex gap-2 flex-shrink-0">
          {Object.entries(prompts).map(([taskId, p]) => (
            <Button
              key={taskId}
              variant={activeTask === taskId ? 'default' : 'outline'}
              size="sm"
              onClick={() => switchTask(taskId)}
            >
              {p.label}
              {p.is_overridden && <span className="ml-1 text-xs">●</span>}
            </Button>
          ))}
        </div>
      )}

      {currentPrompt && (
        <>
          {/* 系统角色 */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                🎭 系统角色 (System Prompt)
                <span className="text-xs text-muted-foreground font-normal">— 定义 Agent 人格</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <textarea
                value={editSystem}
                onChange={e => { setEditSystem(e.target.value); setDirty(true); }}
                className="w-full min-h-[80px] p-3 text-sm rounded border bg-background font-mono resize-y"
                placeholder="你是中线波段交易专家 Agent..."
              />
            </CardContent>
          </Card>

          {/* 任务指令 */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                📋 任务指令 (Task Prompt)
                <span className="text-xs text-muted-foreground font-normal">— 分析步骤 + JSON 输出模板</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <textarea
                value={editTask}
                onChange={e => { setEditTask(e.target.value); setDirty(true); }}
                className="w-full min-h-[400px] p-3 text-sm rounded border bg-background font-mono resize-y"
              />
              <div className="mt-2 text-xs text-muted-foreground">
                💡 提示: <code className="bg-muted px-1 rounded">{'{{variable}}'}</code> 是变量占位符（运行时注入）。
                底部 JSON 模板中的 <Lock className="inline w-3 h-3" /> 字段名不可修改/删除。
              </div>
            </CardContent>
          </Card>

          {/* JSON 输出契约（只读） */}
          <Card className="border-blue-500/20">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Lock className="w-4 h-4 text-blue-500" />
                JSON 输出契约（只读）
                <span className="text-xs text-muted-foreground font-normal">— 以下字段名不可修改</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-1">
                {Object.entries(currentPrompt.schema).map(([field, schema]) => (
                  <div key={field} className="flex items-center gap-2 text-xs font-mono">
                    <span className={`px-1.5 py-0.5 rounded ${schema.required ? 'bg-red-500/10 text-red-500' : 'bg-muted'}`}>
                      {schema.required ? '🔒必填' : '可选'}
                    </span>
                    <span className="font-bold w-32 truncate">"{field}"</span>
                    <span className="text-muted-foreground">: {schema.type}</span>
                    {schema.enum && <span className="text-blue-500">[{schema.enum.join('/')}]</span>}
                    {schema.range && <span className="text-orange-500">[{schema.range[0]}-{schema.range[1]}]</span>}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* 操作按钮 */}
          <div className="flex items-center gap-2 flex-shrink-0">
            <Button variant="outline" size="sm" onClick={handleReset}>
              <RotateCcw className="w-3.5 h-3.5 mr-1" />恢复默认
            </Button>
            <Button variant="outline" size="sm" onClick={handleTest} disabled={testing || dirty}>
              {testing ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1" />}
              测试提示词
            </Button>
            <Button size="sm" onClick={handleSave} disabled={!dirty || saving}>
              {saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
              保存（含结构校验）
            </Button>
            <Button variant="ghost" size="sm" onClick={loadData}>
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
          </div>

          {/* 测试结果 */}
          {testResult && (
            <Card className={testResult.success && testResult.all_fields_present ? 'border-green-500/30' : 'border-red-500/30'}>
              <CardContent className="pt-4 space-y-2">
                <div className="flex items-center gap-2">
                  {testResult.success && testResult.all_fields_present ? (
                    <><CheckCircle2 className="w-5 h-5 text-green-500" /><span className="font-medium text-green-500">测试通过！所有必填字段齐全</span></>
                  ) : (
                    <><AlertTriangle className="w-5 h-5 text-red-500" /><span className="font-medium text-red-500">测试未通过</span></>
                  )}
                </div>

                {testResult.error && <div className="text-sm text-red-500">{testResult.error}</div>}

                {testResult.parsed && (
                  <div className="space-y-1">
                    <div className="text-xs text-muted-foreground">LLM 返回的 JSON:</div>
                    <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-60 font-mono">
                      {JSON.stringify(testResult.parsed, null, 2)}
                    </pre>
                    {testResult.missing_fields && testResult.missing_fields.length > 0 && (
                      <div className="text-xs text-red-500">缺失字段: {testResult.missing_fields.join(', ')}</div>
                    )}
                    {testResult.present_fields && (
                      <div className="text-xs text-green-500">包含字段: {testResult.present_fields.join(', ')}</div>
                    )}
                  </div>
                )}

                {testResult.raw_response && !testResult.json_valid && (
                  <div className="space-y-1">
                    <div className="text-xs text-muted-foreground">原始返回（非JSON）:</div>
                    <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-40">{testResult.raw_response}</pre>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}
      <div className="h-8" />
    </div>
  );
}
