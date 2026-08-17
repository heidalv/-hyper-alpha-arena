"use client";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Settings, Key, Bot, Coins, Shield,
  Plus, Trash2, RefreshCw, CheckCircle2, XCircle, Loader2, Save,
} from "lucide-react";
import { useState, useEffect, useCallback, type ReactNode } from "react";
import { configApi, accountApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/layout/PageHeader";

type Tab = "accounts" | "llm" | "pairs" | "keys" | "gates";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("accounts");

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: "accounts", label: "账户管理", icon: Bot },
    { key: "llm", label: "LLM 配置", icon: Settings },
    { key: "pairs", label: "交易对", icon: Coins },
    { key: "keys", label: "API 密钥", icon: Key },
    { key: "gates", label: "交易门禁", icon: Shield },
  ];

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Settings className="w-4 h-4" />}
        title="设置"
        subtitle="账户 · LLM 配置 · 交易对 · API 密钥 · 交易门禁"
        breadcrumb={[{ label: "系统" }, { label: "设置" }]}
        refreshHint="本地持久化"
      />
      <div className="flex gap-1 border-b border-border overflow-x-auto">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={cn("flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors -mb-px whitespace-nowrap",
                tab === t.key ? "border-primary text-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground")}>
              <Icon className="w-3.5 h-3.5" />{t.label}
            </button>
          );
        })}
      </div>
      {tab === "accounts" && <AccountsTab />}
      {tab === "llm" && <LLMTab />}
      {tab === "pairs" && <PairsTab />}
      {tab === "keys" && <KeysTab />}
      {tab === "gates" && <GatesTab />}
    </div>
  );
}

// ═══ 卡片头（Aurora：标题 + 图标 + 徽章 + 提示 + 右侧操作） ═══
function CardHead({ icon, title, badge, hint, actions }: {
  icon?: ReactNode;
  title: ReactNode;
  badge?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-border/50">
      <div className="flex items-center gap-2.5 min-w-0">
        {icon && (
          <span className="w-7 h-7 shrink-0 rounded-lg bg-gradient-to-br from-cyan-400/20 to-violet-500/20 border border-cyan-400/25 flex items-center justify-center text-cyan-300">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <div className="text-sm font-semibold flex items-center gap-2 flex-wrap">{title}{badge}</div>
          {hint && <div className="text-xs text-muted-foreground mt-0.5">{hint}</div>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">{actions}</div>}
    </div>
  );
}

// ═══ 账户管理 ═══
function AccountsTab() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBalance, setNewBalance] = useState("500");
  const [newMode, setNewMode] = useState("paper");
  const [newExchange, setNewExchange] = useState("asterdex");
  const [newLlm, setNewLlm] = useState("");
  const [newLlmDeep, setNewLlmDeep] = useState("");
  const [llmConfigs, setLlmConfigs] = useState<any[]>([]);
  const [editing, setEditing] = useState<any | null>(null);

  const load = useCallback(async () => {
    try { setAccounts(await accountApi.list()); } catch {} finally { setLoading(false); }
  }, []);
  const loadLlm = useCallback(async () => {
    try { const d = await configApi.llmListAll(); setLlmConfigs(Array.isArray(d) ? d : d.items || []); } catch {}
  }, []);
  useEffect(() => { load(); loadLlm(); }, [load, loadLlm]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await accountApi.create({
        name: newName.trim(), trading_mode: newMode,
        account_type: newMode === "paper" ? "PAPER" : "AI",
        initial_capital: parseFloat(newBalance) || 500,
        selected_exchange: newExchange,
        llm_config_id: newLlm ? parseInt(newLlm) : null,
        llm_config_id_deep: newLlmDeep ? parseInt(newLlmDeep) : null,
      });
      setNewName(""); setNewLlm(""); setNewLlmDeep(""); setShowCreate(false); load();
    } catch (e: any) { alert(e.message); }
  };
  const handleDelete = async (id: number) => {
    if (!confirm("确认删除此账户？")) return;
    try { await accountApi.delete(id); load(); } catch (e: any) { alert(e.message); }
  };
  const handleSaveAccount = async (data: any) => {
    if (!editing) return;
    await accountApi.update(editing.id, data);
    setEditing(null);
    load();
  };
  const defaultCfg = llmConfigs.find((c: any) => c.is_default);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-4">
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<Bot className="w-4 h-4" />}
          title="账户管理"
          badge={<Badge variant="secondary">{accounts.length} 个账户</Badge>}
          hint="新建 · 绑定 LLM · 交易所 · 自动交易开关"
          actions={
            <Button size="sm" onClick={() => setShowCreate(!showCreate)} className="btn-glow"><Plus className="w-3.5 h-3.5 mr-1" />新建账户</Button>
          }
        />
        {showCreate && (
          <div className="px-4 py-4 border-b border-border/50 bg-primary/[0.04]">
            <div className="flex gap-2 items-end flex-wrap">
              <div className="flex-1 min-w-32"><Label className="text-xs">账户名称</Label><Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="如：测试账户" className="text-sm" /></div>
              <div className="w-28"><Label className="text-xs">初始资金</Label><Input type="number" value={newBalance} onChange={(e) => setNewBalance(e.target.value)} className="text-sm" /></div>
              <div className="w-28"><Label className="text-xs">模式</Label>
                <select value={newMode} onChange={(e) => setNewMode(e.target.value)} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                  <option value="paper">模拟</option><option value="live">实盘</option>
                </select>
              </div>
              <div className="w-28"><Label className="text-xs">交易所</Label>
                <select value={newExchange} onChange={(e) => setNewExchange(e.target.value)} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                  <option value="asterdex">Asterdex</option><option value="hyperliquid">Hyperliquid</option>
                  <option value="binance">币安</option><option value="bybit">Bybit</option><option value="okx">OKX</option>
                </select>
              </div>
              <div className="flex-1 min-w-32"><Label className="text-xs">快模型 (Flash)</Label>
                <select value={newLlm} onChange={(e) => setNewLlm(e.target.value)} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                  <option value="">跟随全局默认（{defaultCfg?.model || "无"}）</option>
                  {llmConfigs.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
                </select>
              </div>
              <div className="flex-1 min-w-32"><Label className="text-xs">深模型 (Pro)</Label>
                <select value={newLlmDeep} onChange={(e) => setNewLlmDeep(e.target.value)} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                  <option value="">跟随全局默认（{defaultCfg?.model_deep || defaultCfg?.model || "无"}）</option>
                  {llmConfigs.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model_deep || c.model}</option>)}
                </select>
              </div>
              <Button size="sm" onClick={handleCreate} className="btn-glow">创建</Button>
            </div>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead><tr className="text-muted-foreground border-b border-border">
              <th className="text-left">名称</th><th className="text-left">类型</th>
              <th className="text-right">余额</th><th className="text-left">交易所</th>
              <th className="text-left">模式</th><th className="text-left">LLM 快/深</th>
              <th className="text-center">自动</th><th className="text-center">操作</th>
            </tr></thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id} className="border-b border-border/30 hover:bg-muted/20">
                  <td className="font-medium">{a.name}</td>
                  <td><Badge variant="secondary" className="text-xs">{a.account_type === "PAPER" ? "模拟" : a.account_type === "AI" ? "AI" : a.account_type}</Badge></td>
                  <td className="text-right num">${a.current_cash?.toFixed(2)}</td>
                  <td className="text-muted-foreground">{a.selected_exchange || "—"}</td>
                  <td><Badge variant="secondary" className={cn("text-xs", a.trading_mode === "paper" ? "text-warning" : "text-profit")}>{a.trading_mode === "paper" ? "模拟" : "实盘"}</Badge></td>
                  <td className="text-muted-foreground">
                    <div>{a.llm_config_name || "默认"}{a.llm_config_name_deep ? <span className="text-warning"> / {a.llm_config_name_deep}</span> : null}</div>
                  </td>
                  <td className="text-center">{a.auto_trading_enabled ? <CheckCircle2 className="w-3.5 h-3.5 text-profit mx-auto" /> : <XCircle className="w-3.5 h-3.5 text-muted-foreground mx-auto" />}</td>
                  <td className="text-center">
                    <button onClick={() => setEditing(a)} className="text-primary hover:text-primary/80 mr-2"><Settings className="w-3.5 h-3.5" /></button>
                    <button onClick={() => handleDelete(a.id)} className="text-loss hover:text-loss/80"><Trash2 className="w-3.5 h-3.5" /></button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border/50 bg-muted/20">
                <td colSpan={2} className="text-xs uppercase tracking-wider text-muted-foreground">
                  共 {accounts.length} 个账户
                </td>
                <td className="text-right num font-bold grad-text">
                  ${accounts.reduce((s, a) => s + (a.current_cash || 0), 0).toFixed(2)}
                </td>
                <td colSpan={5} />
              </tr>
            </tfoot>
          </table>
        </div>
      </Card>
      {editing && (
        <AccountLlmEditor
          account={editing}
          llmConfigs={llmConfigs}
          onClose={() => setEditing(null)}
          onSave={handleSaveAccount}
        />
      )}
    </div>
  );
}

function AccountLlmEditor({ account, llmConfigs, onClose, onSave }: {
  account: any; llmConfigs: any[]; onClose: () => void; onSave: (data: any) => Promise<void>;
}) {
  const [form, setForm] = useState({
    name: account.name || "",
    selected_exchange: account.selected_exchange || "asterdex",
    llm_config_id: account.llm_config_id || "",
    llm_config_id_deep: account.llm_config_id_deep || "",
    auto_trading_enabled: !!account.auto_trading_enabled,
  });
  const [saving, setSaving] = useState(false);
  const defaultCfg = llmConfigs.find((c: any) => c.is_default);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave({
        name: form.name,
        selected_exchange: form.selected_exchange,
        llm_config_id: form.llm_config_id ? parseInt(form.llm_config_id) : null,
        llm_config_id_deep: form.llm_config_id_deep ? parseInt(form.llm_config_id_deep) : null,
        auto_trading_enabled: form.auto_trading_enabled,
      });
    } catch (e: any) { alert(e.message); } finally { setSaving(false); }
  };

  return (
    <Card className="p-0 gap-0 border-primary/30">
      <CardHead
        icon={<Settings className="w-4 h-4" />}
        title="编辑账户"
        badge={<Badge variant="secondary">#{account.id}</Badge>}
        hint="LLM 绑定 · 交易所 · 自动交易"
      />
      <div className="px-4 py-4 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div><Label className="text-xs">名称</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="text-sm" /></div>
          <div><Label className="text-xs">交易所</Label>
            <select value={form.selected_exchange} onChange={(e) => setForm({ ...form, selected_exchange: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
              <option value="asterdex">Asterdex</option><option value="hyperliquid">Hyperliquid</option>
              <option value="binance">币安</option><option value="bybit">Bybit</option><option value="okx">OKX</option>
            </select>
          </div>
          <div><Label className="text-xs">快模型配置（Flash / 常规决策）</Label>
            <select value={form.llm_config_id} onChange={(e) => setForm({ ...form, llm_config_id: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
              <option value="">跟随全局默认（{defaultCfg?.model || "无"}）</option>
              {llmConfigs.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
            </select>
          </div>
          <div><Label className="text-xs">深模型配置（Pro / 深度分析）</Label>
            <select value={form.llm_config_id_deep} onChange={(e) => setForm({ ...form, llm_config_id_deep: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
              <option value="">跟随全局默认（{defaultCfg?.model_deep || defaultCfg?.model || "无"}）</option>
              {llmConfigs.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model_deep || c.model}</option>)}
            </select>
          </div>
          <div><Label className="text-xs">自动交易</Label>
            <div className="flex items-center gap-2 pt-1">
              <button onClick={() => setForm({ ...form, auto_trading_enabled: !form.auto_trading_enabled })}
                className={cn("relative w-11 h-6 rounded-full transition-colors", form.auto_trading_enabled ? "bg-primary" : "bg-muted")}>
                <span className={cn("absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform", form.auto_trading_enabled ? "left-5" : "left-0.5")} />
              </button>
              <span className="text-xs">{form.auto_trading_enabled ? "已开启" : "已关闭"}</span>
            </div>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          交易决策：快模型用于常规 tick/执行复核，深模型用于策略分析/Master 决策/AI 选币审核。不指定则跟随全局默认配置。
        </p>
        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" onClick={handleSave} disabled={saving} className="btn-glow">{saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存</Button>
        </div>
      </div>
    </Card>
  );
}

// ═══ LLM 配置 ═══
function LLMTab() {
  const [configs, setConfigs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<any | null>(null);
  const [usages, setUsages] = useState<any[]>([]);

  const load = useCallback(async () => {
    try { const d = await configApi.llmListAll(); setConfigs(Array.isArray(d) ? d : d.items || []); } catch {} finally { setLoading(false); }
  }, []);
  useEffect(() => {
    load();
    configApi.llmUsages().then((d) => setUsages(d.usages || [])).catch(() => {});
  }, [load]);

  const handleDelete = async (id: number) => {
    const c = configs.find((x) => x.id === id);
    const accountsCount = c?.accounts_count ?? 0;
    const profilesCount = c?.profiles_count ?? 0;
    let force = false;
    if (accountsCount + profilesCount > 0) {
      force = confirm(
        `该配置仍被 ${accountsCount} 个账户、${profilesCount} 个套利档案引用。\n` +
        `勾选「强制删除」将自动解除全部关联并删除配置，确认继续？`
      );
      if (!force) return;
    } else {
      if (!confirm("确认删除？")) return;
    }
    try { await configApi.llmDelete(id, force); load(); } catch (e: any) { alert(e.message); }
  };
  const handleTest = async (c: any) => {
    try { await configApi.llmTest({ model: c.model, base_url: c.base_url, api_key: "test" }); alert(`✅ ${c.model} 连接成功`); }
    catch (e: any) { alert(`❌ ${e.message}`); }
  };
  const handleSetDefault = async (c: any) => {
    try { await configApi.llmSetDefault(c.id); load(); } catch (e: any) { alert(e.message); }
  };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;
  if (editing) return <LLMEditor config={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />;

  return (
    <div className="space-y-4">
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<Bot className="w-4 h-4" />}
          title="LLM 配置"
          badge={<Badge variant="secondary">{configs.length} 个配置</Badge>}
          hint="星标 = 全局默认通道；交易用途由「账户管理」绑定优先"
          actions={<Button size="sm" onClick={() => setEditing({ name: "", model: "", model_deep: "", base_url: "", provider: "deepseek" })} className="btn-glow"><Plus className="w-3.5 h-3.5 mr-1" />新增配置</Button>}
        />
        <div className="divide-y divide-border/40">
          {configs.map((c) => (
            <div key={c.id} className="px-4 py-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-8 h-8 shrink-0 rounded-lg bg-primary/10 flex items-center justify-center"><Bot className="w-4 h-4 text-primary" /></div>
                <div className="min-w-0">
                  <div className="text-sm font-medium flex items-center gap-1.5 flex-wrap">
                    {c.name}
                    {c.is_default && <Badge variant="secondary" className="text-warning">默认</Badge>}
                    {!c.is_active && <Badge variant="secondary" className="text-muted-foreground">已停用</Badge>}
                  </div>
                  <div className="text-xs text-muted-foreground truncate">
                    {c.provider} · 快:{c.model}{c.model_deep ? ` · 深:${c.model_deep}` : ""}
                  </div>
                  {c.usage_scope ? <UsageBadges scope={c.usage_scope} usages={usages} /> : null}
                </div>
              </div>
              <div className="flex gap-1 shrink-0 flex-wrap justify-end">
                {!c.is_default && <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => handleSetDefault(c)}>设默认</Button>}
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => handleTest(c)}>测试</Button>
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setEditing(c)}>编辑</Button>
                <Button variant="ghost" size="sm" className="h-7 text-xs text-loss" onClick={() => handleDelete(c.id)}><Trash2 className="w-3 h-3" /></Button>
              </div>
            </div>
          ))}
        </div>
        {configs.length === 0 && <div className="text-center py-8 text-muted-foreground text-sm">暂无 LLM 配置</div>}
      </Card>
    </div>
  );
}

function UsageBadges({ scope, usages }: { scope: string; usages: any[] }) {
  const keys = scope.split(",").map((s) => s.trim()).filter(Boolean);
  const labelOf = (k: string) => (usages.find((u: any) => u.key === k)?.label) || k;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {keys.map((k) => (
        <Badge key={k} variant="outline" className="text-xs">{labelOf(k)}</Badge>
      ))}
    </div>
  );
}

function ModelField({ label, value, onChange, variants, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; variants?: any[]; placeholder?: string;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <div className="flex gap-1">
        <select
          value={variants?.some((v: any) => v.value === value) ? value : ""}
          onChange={(e) => { if (e.target.value) onChange(e.target.value); }}
          className="w-2/5 bg-card border border-border text-sm rounded px-2 py-1.5"
        >
          <option value="">选择预设</option>
          {variants?.map((v: any) => <option key={v.value} value={v.value}>{v.label}</option>)}
        </select>
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder || "或手动输入模型名"} className="text-sm flex-1" />
      </div>
    </div>
  );
}

function LLMEditor({ config, onClose, onSaved }: { config: any; onClose: () => void; onSaved: () => void }) {
  const [providers, setProviders] = useState<any[]>([]);
  const [usages, setUsages] = useState<any[]>([]);
  const [form, setForm] = useState({
    name: config?.name || "",
    provider: config?.provider || "deepseek",
    description: config?.description || "",
    model: config?.model || "",
    model_deep: config?.model_deep || "",
    base_url: config?.base_url || "",
    api_key: "",
    temperature: config?.temperature ?? 0.3,
    is_default: !!config?.is_default,
    is_active: config?.is_active !== false,
    usage_scope: (config?.usage_scope || "").split(",").map((s: string) => s.trim()).filter(Boolean),
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    configApi.llmProviders().then((d) => setProviders(d.providers || [])).catch(() => {});
    configApi.llmUsages().then((d) => setUsages(d.usages || [])).catch(() => {});
  }, []);

  const providerMeta = providers.find((p: any) => p.id === form.provider);
  const allVariants = providerMeta?.model_variants || [];
  const quickVariants = allVariants;
  const deepVariants = allVariants;

  const handleProvider = (pv: string) => {
    const meta = providers.find((p: any) => p.id === pv);
    setForm((f) => ({
      ...f,
      provider: pv,
      model: f.model || meta?.default_model || "",
      base_url: f.base_url || meta?.default_base_url || "",
    }));
  };

  const toggleUsage = (key: string) => {
    setForm((f) => ({
      ...f,
      usage_scope: f.usage_scope.includes(key)
        ? f.usage_scope.filter((k: string) => k !== key)
        : [...f.usage_scope, key],
    }));
  };

  const handleSave = async () => {
    if (!form.name || !form.model) { alert("请填写配置名称和快模型"); return; }
    if (!config?.id && !form.api_key) { alert("请填写 API Key"); return; }
    // [2026-08-15 LLM 统一重构] 模型白名单：provider 有预设变体时禁止自由输入绕过
    if (providerMeta?.model_variants?.length) {
      const allowed = providerMeta.model_variants.map((v: any) => String(v.value).toLowerCase());
      const models = [form.model, form.model_deep].filter(Boolean).map((m) => String(m).toLowerCase());
      const bad = models.find((m) => !allowed.includes(m));
      if (bad) {
        alert(`模型「${bad}」不在 ${form.provider} 白名单内（允许：${allowed.join(", ")}）。\n统一默认 deepseek-v4-flash，禁止界面绕过。`);
        return;
      }
    }
    // 同源去重预检（与后端 400 一致，提前拦截）
    if (!config?.id) {
      try {
        const d = await configApi.llmListAll();
        const existing = (Array.isArray(d) ? d : d.items || []).find(
          (x: any) =>
            x.provider === form.provider &&
            (x.base_url || "").replace(/\/$/, "") === (form.base_url || "").replace(/\/$/, "") &&
            x.is_active !== false
        );
        if (existing) {
          alert(`已存在同源配置「${existing.name}」(id=${existing.id})——同一 provider+base_url 不允许重复创建。\n请复用现有配置或先停用旧配置。`);
          return;
        }
      } catch { /* 后端仍会兜底校验 */ }
    }
    setSaving(true);
    try {
      const data: any = {
        name: form.name,
        provider: form.provider,
        description: form.description,
        model: form.model,
        model_deep: form.model_deep || null,
        base_url: form.base_url,
        is_default: form.is_default,
        is_active: form.is_active,
        usage_scope: form.usage_scope.join(",") || null,
      };
      if (form.api_key) data.api_key = form.api_key;
      if (config?.id) await configApi.llmUpdate(config.id, data); else await configApi.llmCreate(data);
      onSaved();
    } catch (e: any) { alert(e.message); } finally { setSaving(false); }
  };
  return (
    <Card className="p-0 gap-0 border-primary/30">
      <CardHead
        icon={<Settings className="w-4 h-4" />}
        title={config?.id ? "编辑 LLM 配置" : "新增 LLM 配置"}
        badge={config?.id ? <Badge variant="secondary">#{config.id}</Badge> : <Badge variant="outline" className="text-warning">新建</Badge>}
      />
      <div className="px-4 py-4 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div><Label className="text-xs">配置名称</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="text-sm" placeholder="如：DeepSeek V4 (Flash + Pro)" /></div>
          <div><Label className="text-xs">Provider</Label>
            <select value={form.provider} onChange={(e) => handleProvider(e.target.value)} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
              {providers.length === 0 && <option value="deepseek">DeepSeek</option>}
              {providers.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div className="col-span-2">
            <ModelField label="快模型（Flash / 常规决策）" value={form.model} onChange={(v) => setForm({ ...form, model: v })}
              variants={quickVariants} placeholder="如：deepseek-v4-flash" />
          </div>
          <div className="col-span-2">
            <ModelField label="深模型（统一也用 deepseek-v4-flash）" value={form.model_deep} onChange={(v) => setForm({ ...form, model_deep: v })}
              variants={deepVariants} placeholder="如：deepseek-v4-flash" />
          </div>
          {providerMeta?.model_variants?.length ? (
            <div className="col-span-2 text-xs text-muted-foreground -mt-1">
              模型受白名单约束（{providerMeta.model_variants.map((v: any) => v.value).join(" / ")}）。统一默认 deepseek-v4-flash；换模型须先改后端白名单。
            </div>
          ) : null}
          <div><Label className="text-xs">描述</Label><Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="text-sm" placeholder="用途说明（可选）" /></div>
          <div><Label className="text-xs">Base URL</Label><Input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} className="text-sm" placeholder="https://api.deepseek.com" /></div>
          <div><Label className="text-xs">API Key {config?.id ? "(留空不改)" : ""}</Label><Input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} className="text-sm" placeholder="sk-..." /></div>
          <div><Label className="text-xs">Temperature</Label><Input type="number" step="0.1" value={form.temperature} onChange={(e) => setForm({ ...form, temperature: parseFloat(e.target.value) })} className="text-sm" /></div>
          <div className="col-span-2 grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs">设为全局默认</Label>
              <div className="flex items-center gap-2 pt-1">
                <button onClick={() => setForm({ ...form, is_default: !form.is_default })}
                  className={cn("relative w-11 h-6 rounded-full transition-colors", form.is_default ? "bg-warning" : "bg-muted")}>
                  <span className={cn("absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform", form.is_default ? "left-5" : "left-0.5")} />
                </button>
                <span className="text-xs">{form.is_default ? "已设为默认" : "未设默认"}</span>
              </div>
            </div>
            {config?.id && (
              <div>
                <Label className="text-xs">启用状态</Label>
                <div className="flex items-center gap-2 pt-1">
                  <button onClick={() => setForm({ ...form, is_active: !form.is_active })}
                    className={cn("relative w-11 h-6 rounded-full transition-colors", form.is_active ? "bg-primary" : "bg-muted")}>
                    <span className={cn("absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform", form.is_active ? "left-5" : "left-0.5")} />
                  </button>
                  <span className="text-xs">{form.is_active ? "启用" : "停用"}</span>
                </div>
              </div>
            )}
          </div>
          <div className="col-span-2">
            <Label className="text-xs">用途分配（后台指定非交易 LLM 用途）</Label>
            <div className="grid grid-cols-2 gap-1 mt-1">
              {usages.map((u: any) => (
                <label key={u.key} className="flex items-center gap-1.5 text-xs" title={u.description}>
                  <input type="checkbox" checked={form.usage_scope.includes(u.key)} onChange={() => toggleUsage(u.key)} />
                  <span className="font-medium">{u.label}</span>
                  <span className="text-muted-foreground text-xs truncate">{u.key}</span>
                </label>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              不分配 = 通用后备；「交易决策」始终由账户绑定优先。同一配置可分配多个用途。
            </p>
          </div>
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
          <Button size="sm" onClick={handleSave} disabled={saving} className="btn-glow">{saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存</Button>
        </div>
      </div>
    </Card>
  );
}

// ═══ 交易对管理 ═══
function PairsTab() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [builtin, setBuiltin] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [newSymbol, setNewSymbol] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try { const d = await configApi.tradingPairs(); setSymbols(d.symbols || []); setBuiltin(d.exchange_symbols || d.builtin || []); } catch {} finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const handleAdd = (s: string) => { s = s.trim().toUpperCase(); if (s && !symbols.includes(s)) setSymbols([...symbols, s]); setNewSymbol(""); };
  const handleSave = async () => { setSaving(true); try { await configApi.saveTradingPairs(symbols); } catch (e: any) { alert(e.message); } finally { setSaving(false); } };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-4">
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<Coins className="w-4 h-4" />}
          title="交易对"
          badge={<Badge variant="secondary">{symbols.length} 个已配置</Badge>}
          hint="已保存到本地配置，保存后即时生效"
          actions={<Button size="sm" onClick={handleSave} disabled={saving} className="btn-glow">{saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存</Button>}
        />
        <div className="px-4 py-4">
          {symbols.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {symbols.map((s) => (
                <span key={s} className="chip-capsule">{s}
                  <button onClick={() => setSymbols(symbols.filter(x => x !== s))} className="text-muted-foreground hover:text-loss"><XCircle className="w-3.5 h-3.5" /></button>
                </span>
              ))}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">暂无已配置交易对</div>
          )}
        </div>
      </Card>
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<Plus className="w-4 h-4" />}
          title="添加交易对"
          hint="输入币种后回车或点击 +；下方为交易所内置币种（点击加入）"
        />
        <div className="px-4 py-4">
          <div className="flex gap-2 mb-3"><Input value={newSymbol} onChange={(e) => setNewSymbol(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleAdd(newSymbol)} placeholder="输入币种" className="text-sm" /><Button size="sm" onClick={() => handleAdd(newSymbol)}><Plus className="w-3.5 h-3.5" /></Button></div>
          {builtin.length > 0 && (
            <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
              {builtin.filter(s => !symbols.includes(s)).slice(0, 100).map(s => (
                <button key={s} onClick={() => handleAdd(s)} className="chip-capsule hover:text-cyan-300 hover:border-cyan-400/30">{s}</button>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

// ═══ API 密钥 ═══
function KeysTab() {
  const [keys, setKeys] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => { try { setKeys(await configApi.externalKeys()); } catch {} finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  const handleSave = async (k: string) => { setSaving(true); try { await configApi.saveExternalKey(k, editValue); setEditingKey(null); setEditValue(""); load(); } catch (e: any) { alert(e.message); } finally { setSaving(false); } };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="grid gap-4 md:grid-cols-2 items-start">
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<Key className="w-4 h-4" />}
          title="外部 API 密钥"
          hint="AES-256 加密存储"
        />
        <div className="px-4 py-4">
          {Object.keys(keys).length === 0 ? (
            <div className="text-xs text-muted-foreground">暂无外部密钥</div>
          ) : (
            <div className="space-y-2">
              {Object.entries(keys).map(([key, info]: [string, any]) => (
                <div key={key} className="flex items-center justify-between py-2 border-b border-border/30 last:border-0">
                  <div className="flex items-center gap-2 min-w-0">
                    <Key className={cn("w-3.5 h-3.5 shrink-0", info.configured ? "text-profit" : "text-muted-foreground")} />
                    <div className="min-w-0"><div className="text-sm">{info.label || key}</div><div className="text-xs text-muted-foreground font-mono truncate">{info.configured ? info.masked || "已配置" : "未配置"}</div></div>
                  </div>
                  {editingKey === key ? (
                    <div className="flex gap-1 shrink-0">
                      <Input type="password" value={editValue} onChange={(e) => setEditValue(e.target.value)} placeholder="输入密钥..." className="w-40 text-xs h-7" />
                      <Button size="sm" className="h-7 btn-glow" onClick={() => handleSave(key)} disabled={saving || !editValue}>保存</Button>
                      <Button size="sm" variant="ghost" className="h-7" onClick={() => { setEditingKey(null); setEditValue(""); }}>取消</Button>
                    </div>
                  ) : (
                    <Button size="sm" variant="outline" className="h-7 text-xs shrink-0" onClick={() => { setEditingKey(key); setEditValue(""); }}>{info.configured ? "修改" : "配置"}</Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<CheckCircle2 className="w-4 h-4" />}
          title="必需配置检查"
          hint="启动前自检"
        />
        <div className="px-4 py-4">
          <RequiredCheck />
        </div>
      </Card>
    </div>
  );
}

function RequiredCheck() {
  const [data, setData] = useState<any>(null);
  useEffect(() => { configApi.checkRequired().then(setData).catch(() => {}); }, []);
  if (!data) return <div className="text-xs text-muted-foreground">检查中...</div>;
  return <div className="flex items-center gap-2">{data.has_required_configs ? <><CheckCircle2 className="w-4 h-4 text-profit" /><span className="text-sm text-profit">所有必需配置已就绪</span></> : <><XCircle className="w-4 h-4 text-loss" /><span className="text-sm text-loss">缺失: {data.missing_configs?.join(", ")}</span></>}</div>;
}

// ═══ 交易门禁 ═══
function GatesTab() {
  const [gates, setGates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => { try { const d = await configApi.tradingGates(); setGates(d.gates || []); } catch {} finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaving(true);
    try { const updates: Record<string, number> = {}; gates.forEach(g => { updates[g.key] = g.current; }); await configApi.saveTradingGates({ gates: updates }); } catch (e: any) { alert(e.message); } finally { setSaving(false); }
  };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-4">
      <Card className="p-0 gap-0 overflow-hidden">
        <CardHead
          icon={<Shield className="w-4 h-4" />}
          title="交易门禁"
          badge={<Badge variant="secondary">{gates.length} 项</Badge>}
          hint="生效范围：全部实盘账户"
          actions={<Button size="sm" onClick={handleSave} disabled={saving} className="btn-glow">{saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存门禁</Button>}
        />
        <div className="px-4 py-4 space-y-3">
          {gates.map((g, idx) => (
            <div key={g.key} className="rounded-lg bg-white/[0.04] border border-white/10 px-4 py-3">
              <div className="flex items-center justify-between mb-1 gap-3">
                <div className="flex items-center gap-2 min-w-0 flex-wrap">
                  <Badge variant="secondary" className={cn("text-xs", g.category === "hard" ? "text-loss" : "text-warning")}>{g.category}</Badge>
                  <span className="text-sm font-medium">{g.name}</span><span className="text-xs text-muted-foreground">{g.layer}</span>
                </div>
                <span className="text-sm font-bold tabular-nums shrink-0">{g.type === "float" ? g.current?.toFixed(2) : g.current}</span>
              </div>
              {g.desc && <p className="text-xs text-muted-foreground mb-2">{g.desc}</p>}
              <input type="range" min={g.min} max={g.max} step={g.type === "float" ? (g.max - g.min) / 100 : 1} value={g.current} onChange={(e) => setGates(gates.map((gg, i) => i === idx ? { ...gg, current: parseFloat(e.target.value) } : gg))} className="w-full" />
              <div className="flex justify-between text-xs text-muted-foreground"><span>{g.min}</span><span className="text-muted-foreground/60">默认 {g.default}</span><span>{g.max}</span></div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
