"use client";

import { useEffect, useState, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Server, Plus, Trash2, Loader2, CheckCircle2, XCircle, RefreshCw,
  Key, Bot, Settings2, Link2, Save, AlertTriangle,
  Wallet, Banknote, TrendingUp,
} from "lucide-react";
import { useAccounts, useCreateAccount, useDeleteAccount, useUpdateAccount } from "@/hooks/useTradingData";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
const BACKEND = getBackendUrl().replace(/\/$/, "");

const EX_NAMES: Record<string, string> = {
  hyperliquid: "Hyperliquid", binance: "币安", bybit: "Bybit",
  okx: "OKX", gateio: "Gate.io", asterdex: "Asterdex",
};
const exName = (id: string) => EX_NAMES[id] || id;

type Tab = "accounts" | "credentials" | "monitor";

export default function ExchangePage() {
  const [tab, setTab] = useState<Tab>("accounts");

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: "accounts", label: "账户管理", icon: Bot },
    { key: "credentials", label: "API 凭证", icon: Key },
    { key: "monitor", label: "交易所监控", icon: Server },
  ];

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Server className="w-4 h-4" />}
        title="交易所管理"
        subtitle="多交易所账户 · API 凭证 · 连接监控"
        refreshHint="连接状态实时"
        breadcrumb={[{ label: "交易所" }, { label: "交易所管理" }]}
      />
      <div className="flex gap-1 border-b border-border overflow-x-auto">
        {tabs.map(t => {
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
      {tab === "credentials" && <CredentialsTab />}
      {tab === "monitor" && <MonitorTab />}
    </div>
  );
}

// ═══ 账户管理（含交易所分配+LLM配置+人格） ═══
function AccountsTab() {
  const { data: accounts, isLoading } = useAccounts();
  const createMut = useCreateAccount();
  const deleteMut = useDeleteAccount();
  const updateMut = useUpdateAccount();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const [llmConfigs, setLlmConfigs] = useState<any[]>([]);
  const [personalities, setPersonalities] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${BACKEND}/api/llm-configs`).then(r => r.json()).then(d => setLlmConfigs(d.items || [])).catch(() => {});
    fetch(`${BACKEND}/api/account/personality-presets`).then(r => r.json()).then(setPersonalities).catch(() => {});
  }, []);

  const [form, setForm] = useState({ name: "", trading_mode: "paper", initial_capital: "500", selected_exchange: "asterdex", llm_config_id: "", llm_config_id_deep: "", personality_id: "" });

  const handleCreate = async () => {
    if (!form.name.trim()) return;
    await createMut.mutateAsync({
      name: form.name.trim(), trading_mode: form.trading_mode,
      account_type: form.trading_mode === "paper" ? "PAPER" : "AI",
      initial_capital: parseFloat(form.initial_capital) || 500,
      selected_exchange: form.selected_exchange,
      llm_config_id: form.llm_config_id ? parseInt(form.llm_config_id) : null,
      llm_config_id_deep: form.llm_config_id_deep ? parseInt(form.llm_config_id_deep) : null,
    } as any);
    setForm({ name: "", trading_mode: "paper", initial_capital: "500", selected_exchange: "asterdex", llm_config_id: "", llm_config_id_deep: "", personality_id: "" });
    setShowCreate(false);
  };
  const defaultCfg = llmConfigs.find((c: any) => c.is_default);

  if (isLoading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-3">
      {/* 创建表单 */}
      {showCreate && (
        <Card className="p-4 border-primary/30 space-y-3 glass">
          <div className="flex items-center gap-2.5">
            <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/20 to-violet-500/20 border border-cyan-400/25 flex items-center justify-center text-cyan-300 flex-shrink-0">
              <Plus className="w-3.5 h-3.5" />
            </span>
            <div>
              <div className="text-sm font-medium">新建账户</div>
              <div className="text-xs text-muted-foreground">创建后需在「API 凭证」中补充密钥</div>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <div><Label className="text-xs">账户名称</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如：BTC趋势" className="text-sm" /></div>
            <div><Label className="text-xs">模式</Label>
              <select value={form.trading_mode} onChange={(e) => setForm({ ...form, trading_mode: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="paper">模拟</option><option value="live">实盘</option>
              </select>
            </div>
            <div><Label className="text-xs">初始资金</Label><Input type="number" value={form.initial_capital} onChange={(e) => setForm({ ...form, initial_capital: e.target.value })} className="text-sm" /></div>
            <div><Label className="text-xs">交易所</Label>
              <select value={form.selected_exchange} onChange={(e) => setForm({ ...form, selected_exchange: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="asterdex">Asterdex</option><option value="hyperliquid">Hyperliquid</option>
                <option value="binance">币安</option><option value="bybit">Bybit</option><option value="okx">OKX</option>
              </select>
            </div>
            <div><Label className="text-xs">LLM 配置</Label>
              <select value={form.llm_config_id} onChange={(e) => setForm({ ...form, llm_config_id: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="">跟随全局默认（{defaultCfg?.model || "无"}）</option>
                {llmConfigs.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
              </select>
            </div>
            <div><Label className="text-xs">深模型 LLM 配置 (Pro)</Label>
              <select value={form.llm_config_id_deep} onChange={(e) => setForm({ ...form, llm_config_id_deep: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="">跟随全局默认（{defaultCfg?.model_deep || defaultCfg?.model || "无"}）</option>
                {llmConfigs.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model_deep || c.model}</option>)}
              </select>
            </div>
            <div><Label className="text-xs">交易员人格</Label>
              <select value={form.personality_id} onChange={(e) => setForm({ ...form, personality_id: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="">默认</option>
                {personalities.map((p: any) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
              </select>
            </div>
          </div>
          <Button size="sm" className="btn-glow" onClick={handleCreate} disabled={createMut.isPending || !form.name.trim()}>
            {createMut.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "创建"}
          </Button>
        </Card>
      )}

      {/* 账户表格 */}
      <Card className="overflow-hidden glass p-0">
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border/60">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/20 to-violet-500/20 border border-cyan-400/25 flex items-center justify-center text-cyan-300 flex-shrink-0">
              <Wallet className="w-3.5 h-3.5" />
            </span>
            <div className="min-w-0">
              <div className="text-sm font-medium">账户列表</div>
              <div className="text-xs text-muted-foreground">
                {accounts?.length ?? 0} 个账户 · {accounts?.filter((a) => a.auto_trading_enabled).length ?? 0} 个已启用自动交易
              </div>
            </div>
          </div>
          <Button size="sm" className="btn-glow flex-shrink-0" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="w-3.5 h-3.5 mr-1" />新建账户
          </Button>
        </div>
        <table className="data-table">
          <thead><tr className="text-muted-foreground border-b border-border">
            <th className="text-left py-2 px-3">名称</th><th className="text-left py-2 px-3">类型</th>
            <th className="text-right py-2 px-3">余额 <span className="text-cyan-300">▲</span></th><th className="text-left py-2 px-3">交易所</th>
            <th className="text-left py-2 px-3">模式</th><th className="text-left py-2 px-3">LLM</th>
            <th className="text-center py-2 px-3">自动</th><th className="text-center py-2 px-3">操作</th>
          </tr></thead>
          <tbody>
            {(!accounts || accounts.length === 0) && (
              <tr><td colSpan={8} className="py-8 text-center text-muted-foreground text-xs">暂无账户，点击右上角「新建账户」开始</td></tr>
            )}
            {accounts?.map((a) => (
              <tr key={a.id} className="border-b border-border/30 hover:bg-muted/20">
                <td className="py-2 px-3 font-medium">{a.name}</td>
                <td className="py-2 px-3"><Badge variant="secondary" className="text-xs">{a.account_type === "PAPER" ? "模拟" : a.account_type === "AI" ? "AI" : a.account_type}</Badge></td>
                <td className="py-2 px-3 text-right num">${(a.current_cash || 0).toFixed(2)}</td>
                <td className="py-2 px-3"><Badge variant="secondary" className="text-xs text-primary">{exName(a.selected_exchange || "")}</Badge></td>
                <td className="py-2 px-3"><Badge variant="secondary" className={cn("text-xs", a.trading_mode === "paper" ? "text-warning" : "text-profit")}>{a.trading_mode === "paper" ? "模拟" : "实盘"}</Badge></td>
                <td className="py-2 px-3 text-muted-foreground">
                  <div>快:{a.llm_config_name || "默认"}</div>
                  {a.llm_config_name_deep ? <div className="text-warning">深:{a.llm_config_name_deep}</div> : null}
                </td>
                <td className="py-2 px-3 text-center">{a.auto_trading_enabled ? <CheckCircle2 className="w-3.5 h-3.5 text-profit mx-auto" /> : <XCircle className="w-3.5 h-3.5 text-muted-foreground mx-auto" />}</td>
                <td className="py-2 px-3 text-center">
                  <button onClick={() => setEditing(a)} className="text-primary hover:text-primary/80 mr-1"><Settings2 className="w-3.5 h-3.5" /></button>
                  <button onClick={() => { if (confirm("删除？")) deleteMut.mutate(a.id); }} className="text-loss hover:text-loss/80"><Trash2 className="w-3.5 h-3.5" /></button>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-border/50 bg-muted/20">
              <td colSpan={6} className="px-3 py-2 text-xs text-muted-foreground">
                合计 <span className="num font-semibold text-foreground">{accounts?.length ?? 0}</span> 账户
              </td>
              <td colSpan={2} className="px-3 py-2 text-center text-xs text-muted-foreground">
                实盘 <span className="num font-semibold text-profit">{accounts?.filter((a) => a.trading_mode === "live").length ?? 0}</span>
                {" · "}模拟 <span className="num font-semibold text-warning">{accounts?.filter((a) => a.trading_mode === "paper").length ?? 0}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </Card>

      {/* 编辑账户弹窗 */}
      {editing && (
        <AccountEditor
          account={editing}
          llmConfigs={llmConfigs}
          personalities={personalities}
          onClose={() => setEditing(null)}
          onSave={async (data: any) => {
            await updateMut.mutateAsync({ id: editing.id, data });
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function AccountEditor({ account, llmConfigs, personalities, onClose, onSave }: any) {
  const [form, setForm] = useState({
    name: account.name,
    selected_exchange: account.selected_exchange || "asterdex",
    llm_config_id: account.llm_config_id || "",
    llm_config_id_deep: account.llm_config_id_deep || "",
    auto_trading_enabled: account.auto_trading_enabled,
  });
  const [saving, setSaving] = useState(false);
  const defaultCfg = (llmConfigs || []).find((c: any) => c.is_default);

  const handleSave = async () => {
    setSaving(true);
    await onSave({
      name: form.name,
      selected_exchange: form.selected_exchange,
      llm_config_id: form.llm_config_id ? parseInt(form.llm_config_id) : null,
      llm_config_id_deep: form.llm_config_id_deep ? parseInt(form.llm_config_id_deep) : null,
      auto_trading_enabled: form.auto_trading_enabled,
    });
    setSaving(false);
  };

  return (
    <Card className="p-4 border-primary/30 space-y-3 glass">
      <div className="flex items-center gap-2.5">
        <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/20 to-violet-500/20 border border-cyan-400/25 flex items-center justify-center text-cyan-300 flex-shrink-0">
          <Settings2 className="w-3.5 h-3.5" />
        </span>
        <div>
          <div className="text-sm font-medium">编辑账户 #{account.id}</div>
          <div className="text-xs text-muted-foreground">修改后保存立即生效</div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><Label className="text-xs">名称</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="text-sm" /></div>
        <div><Label className="text-xs">交易所</Label>
          <select value={form.selected_exchange} onChange={(e) => setForm({ ...form, selected_exchange: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
            <option value="asterdex">Asterdex</option><option value="hyperliquid">Hyperliquid</option>
            <option value="binance">币安</option><option value="bybit">Bybit</option><option value="okx">OKX</option>
          </select>
        </div>
        <div><Label className="text-xs">LLM 配置</Label>
          <select value={form.llm_config_id} onChange={(e) => setForm({ ...form, llm_config_id: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
            <option value="">跟随全局默认（{defaultCfg?.model || "无"}）</option>
            {llmConfigs.map((c: any) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
          </select>
        </div>
        <div><Label className="text-xs">深模型 LLM 配置 (Pro)</Label>
          <select value={form.llm_config_id_deep} onChange={(e) => setForm({ ...form, llm_config_id_deep: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
            <option value="">跟随全局默认（{defaultCfg?.model_deep || defaultCfg?.model || "无"}）</option>
            {llmConfigs.map((c: any) => <option key={c.id} value={c.id}>{c.name} · {c.model_deep || c.model}</option>)}
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
      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
        <Button size="sm" className="btn-glow" onClick={handleSave} disabled={saving}>{saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存</Button>
      </div>
    </Card>
  );
}

// ═══ 交易所监控（融合连接状态+余额+持仓） ═══
function MonitorTab() {
  const [statuses, setStatuses] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [balances, setBalances] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sts, allPos] = await Promise.all([
        fetch(`${BACKEND}/api/exchange/statuses`).then(r => r.json()).catch(() => []),
        fetch(`${BACKEND}/api/exchange/positions/all`).then(r => r.json()).catch(() => []),
      ]);
      setStatuses(sts);
      setPositions(Array.isArray(allPos) ? allPos : (allPos?.positions || []));
      const balResults: Record<string, any> = {};
      await Promise.all(sts.filter((s: any) => s.connected).map(async (s: any) => {
        try { balResults[s.exchange] = await fetch(`/api/exchange/${s.exchange}/balance`).then(r => r.json()); } catch {}
      }));
      setBalances(balResults);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);

  const EX_NAMES: Record<string, string> = { hyperliquid: "Hyperliquid", binance: "币安", bybit: "Bybit", okx: "OKX", gateio: "Gate.io", asterdex: "Asterdex" };
  const getExName = (id: string) => EX_NAMES[id] || id;
  const connectedCount = statuses.filter(s => s.connected).length;

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className={cn("chip-capsule", connectedCount > 0 && connectedCount === statuses.length && statuses.length > 0 ? "ws" : "")}>{connectedCount}/{statuses.length} 已连接</span>
        <Button variant="ghost" size="sm" onClick={load}><RefreshCw className="w-3.5 h-3.5" /></Button>
      </div>

      {/* KPI 卡片化：跨所总权益 / 可用 / 已用保证金 / 持仓 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <Wallet className="w-3.5 h-3.5" />
          </span>
          <div className="text-xs text-muted-foreground">总权益</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight grad-text">${Object.values(balances).reduce((s: number, b: any) => s + (b.total_equity || 0), 0).toFixed(2)}</div>
        </Card>
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <Banknote className="w-3.5 h-3.5" />
          </span>
          <div className="text-xs text-muted-foreground">可用</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight">${Object.values(balances).reduce((s: number, b: any) => s + (b.available_balance || 0), 0).toFixed(2)}</div>
        </Card>
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <Server className="w-3.5 h-3.5" />
          </span>
          <div className="text-xs text-muted-foreground">已用保证金</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight">${Object.values(balances).reduce((s: number, b: any) => s + (b.used_margin || 0), 0).toFixed(2)}</div>
        </Card>
        <Card className="relative p-3 glass">
          <span className="absolute right-3 top-3 w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300">
            <TrendingUp className="w-3.5 h-3.5" />
          </span>
          <div className="text-xs text-muted-foreground">持仓</div>
          <div className="text-lg font-bold font-mono tabular-nums tracking-tight leading-tight">{positions.length}</div>
          <div className="text-xs text-muted-foreground">{connectedCount}/{statuses.length} 已连接</div>
        </Card>
      </div>

      {/* 交易所卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {statuses.map((s) => {
          const bal = balances[s.exchange];
          const exPositions = positions.filter((p: any) => p.exchange === s.exchange);
          const connected = s.connected;
          return (
            <Card key={s.exchange} className={cn("p-4 border", connected ? "border-profit/30" : "border-border")}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <div className={cn("w-8 h-8 rounded flex items-center justify-center", connected ? "bg-profit/10" : "bg-muted")}>
                    <Server className={cn("w-4 h-4", connected ? "text-profit" : "text-muted-foreground")} />
                  </div>
                  <div>
                    <div className="text-sm font-medium">{getExName(s.exchange)}</div>
                    <div className="text-xs text-muted-foreground">{s.supports_spot && "现货"} {s.supports_futures && "合约"}</div>
                  </div>
                </div>
                <Badge variant="secondary" className={cn("text-xs", connected ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>{connected ? "已连接" : "未连接"}</Badge>
              </div>

              {connected && bal && (
                <div className="space-y-1 mb-3">
                  {bal.total_equity != null && <div className="flex justify-between text-xs"><span className="text-muted-foreground">总权益</span><span className="font-bold tabular-nums grad-text">${(bal.total_equity || 0).toFixed(2)}</span></div>}
                  {bal.available_balance != null && <div className="flex justify-between text-xs"><span className="text-muted-foreground">可用</span><span className="tabular-nums">${(bal.available_balance || 0).toFixed(2)}</span></div>}
                  {bal.used_margin != null && bal.used_margin > 0 && <div className="flex justify-between text-xs"><span className="text-muted-foreground">已用保证金</span><span className="tabular-nums">${(bal.used_margin || 0).toFixed(2)}</span></div>}
                </div>
              )}

              {connected && exPositions.length > 0 && (
                <div className="pt-2 border-t border-border/30">
                  <div className="text-xs text-muted-foreground mb-1">持仓 ({exPositions.length})</div>
                  <div className="space-y-0.5 max-h-32 overflow-y-auto">
                    {exPositions.slice(0, 8).map((p: any, i: number) => {
                      const pnl = p.unrealized_pnl || p.pnl || 0;
                      const isLong = (p.side || p.position_side) === "long" || (p.side || p.position_side) === "buy";
                      return (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <div className="flex items-center gap-1">
                            <span className="font-medium">{p.symbol || "—"}</span>
                            <span className={cn("text-xs px-1 rounded", isLong ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>{isLong ? "多" : "空"}</span>
                          </div>
                          <span className={cn("tabular-nums", pnl >= 0 ? "text-profit" : "text-loss")}>{pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {!connected && <div className="text-center py-2 text-xs text-muted-foreground">在「API 凭证」中配置连接</div>}
            </Card>
          );
        })}
      </div>

      {/* 跨所持仓表 */}
      {positions.length > 0 && (
        <Card className="overflow-hidden glass p-0">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border/60">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="w-7 h-7 rounded-lg bg-gradient-to-br from-cyan-400/20 to-violet-500/20 border border-cyan-400/25 flex items-center justify-center text-cyan-300 flex-shrink-0">
                <TrendingUp className="w-3.5 h-3.5" />
              </span>
              <div>
                <div className="text-sm font-medium">跨所持仓</div>
                <div className="text-xs text-muted-foreground">{positions.length} 个仓位</div>
              </div>
            </div>
            <span className="chip-capsule flex-shrink-0">{positions.length} 持仓</span>
          </div>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr className="text-muted-foreground border-b border-border">
                <th className="text-left py-2 px-2">交易所</th><th className="text-left py-2 px-2">币种</th><th className="text-left py-2 px-2">方向</th>
                <th className="text-right py-2 px-2">数量 <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">入场价 <span className="text-cyan-300">▲</span></th><th className="text-right py-2 px-2">浮盈 <span className="text-cyan-300">▲</span></th>
              </tr></thead>
              <tbody>
                {positions.map((p: any, i: number) => {
                  const pnl = p.unrealized_pnl || p.pnl || 0;
                  const isLong = (p.side || p.position_side) === "long" || (p.side || p.position_side) === "buy";
                  return (
                    <tr key={i} className="border-b border-border/30 hover:bg-muted/20">
                      <td className="py-2 px-2"><Badge variant="secondary" className="text-xs">{getExName(p.exchange)}</Badge></td>
                      <td className="py-2 px-2 font-medium">{p.symbol}</td>
                      <td className="py-2 px-2"><span className={cn("text-xs px-1 rounded", isLong ? "text-profit bg-profit/10" : "text-loss bg-loss/10")}>{isLong ? "多" : "空"}</span></td>
                      <td className="py-2 px-2 text-right num">{(p.quantity || p.size || 0).toFixed(4)}</td>
                      <td className="py-2 px-2 text-right num text-muted-foreground">{(p.entry_price || 0).toLocaleString()}</td>
                      <td className={cn("py-2 px-2 text-right num font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>{pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}</td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-border/50 bg-muted/20">
                  <td colSpan={4} className="px-2 py-2 text-xs text-muted-foreground">
                    合计 <span className="num font-semibold text-foreground">{positions.length}</span> 持仓
                  </td>
                  <td colSpan={2} className="px-2 py-2 text-right text-xs text-muted-foreground">
                    {(() => {
                      const total = positions.reduce((s: number, p: any) => s + (p.unrealized_pnl || p.pnl || 0), 0);
                      return (
                        <>
                          浮盈{" "}
                          <span className={cn("num font-semibold", total >= 0 ? "text-profit" : "text-loss")}>
                            {total >= 0 ? "+" : ""}${total.toFixed(2)}
                          </span>
                        </>
                      );
                    })()}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

// ═══ API 凭证管理 ═══
function CredentialsTab() {
  const [credentials, setCredentials] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ exchange: "binance", api_key: "", api_secret: "", passphrase: "", label: "" });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setCredentials(await fetch(`${BACKEND}/api/exchange/credentials`).then(r => r.json()).catch(() => [])); }
    catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`${BACKEND}/api/exchange/credentials`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      setShowAdd(false);
      setForm({ exchange: "binance", api_key: "", api_secret: "", passphrase: "", label: "" });
      load();
    } catch (e: any) { alert(e.message); }
    setSaving(false);
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    try {
      const result = await fetch(`/api/exchange/credentials/${id}/test`, { method: "POST" }).then(r => r.json());
      alert(result.connected ? "✅ 连接成功" : `❌ ${result.error || "连接失败"}`);
    } catch (e: any) { alert(e.message); }
    setTesting(null);
  };

  const handleDelete = async (id: number) => {
    if (!confirm("确认删除此凭证？")) return;
    try { await fetch(`/api/exchange/credentials/${id}`, { method: "DELETE" }); load(); }
    catch (e: any) { alert(e.message); }
  };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button size="sm" className="btn-glow" onClick={() => setShowAdd(!showAdd)}><Plus className="w-3.5 h-3.5 mr-1" />添加凭证</Button>
      </div>

      {showAdd && (
        <Card className="p-4 border-primary/30 space-y-3">
          <div className="text-sm font-medium">添加交易所 API 凭证</div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label className="text-xs">交易所</Label>
              <select value={form.exchange} onChange={(e) => setForm({ ...form, exchange: e.target.value })}
                className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="binance">币安</option><option value="bybit">Bybit</option>
                <option value="okx">OKX</option><option value="gateio">Gate.io</option>
                <option value="hyperliquid">Hyperliquid</option>
              </select>
            </div>
            <div><Label className="text-xs">标签 (可选)</Label><Input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="如：主账户" className="text-sm" /></div>
            <div><Label className="text-xs">API Key</Label><Input value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} className="text-sm font-mono" placeholder="输入 API Key" /></div>
            <div><Label className="text-xs">API Secret</Label><Input type="password" value={form.api_secret} onChange={(e) => setForm({ ...form, api_secret: e.target.value })} className="text-sm font-mono" placeholder="输入 Secret" /></div>
            {form.exchange === "okx" && (
              <div className="col-span-2"><Label className="text-xs">Passphrase (仅 OKX)</Label><Input type="password" value={form.passphrase} onChange={(e) => setForm({ ...form, passphrase: e.target.value })} className="text-sm font-mono" /></div>
            )}
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={() => setShowAdd(false)}>取消</Button>
            <Button size="sm" className="btn-glow" onClick={handleSave} disabled={saving || !form.api_key}>{saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}保存</Button>
          </div>
        </Card>
      )}

      {/* 凭证列表 */}
      {credentials.length === 0 ? (
        <Card className="p-8 text-center text-muted-foreground text-sm glass">
          <div className="w-11 h-11 mx-auto mb-2 rounded-xl bg-gradient-to-br from-cyan-400/15 to-violet-500/15 border border-cyan-400/25 flex items-center justify-center">
            <Key className="w-5 h-5 text-cyan-300" />
          </div>
          暂无交易所 API 凭证，点击「添加凭证」配置
        </Card>
      ) : (
        <div className="space-y-2">
          {credentials.map((cred) => (
            <Card key={cred.id} className="p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded bg-primary/10 flex items-center justify-center">
                    <Key className="w-4 h-4 text-primary" />
                  </div>
                  <div>
                    <div className="text-sm font-medium">{cred.exchange_name || cred.exchange} {cred.label && `· ${cred.label}`}</div>
                    <div className="text-xs text-muted-foreground font-mono">
                      {cred.api_key_masked || `${cred.api_key?.slice(0, 6)}...`}
                      {cred.account_id && ` · 账户 #${cred.account_id}`}
                    </div>
                  </div>
                </div>
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => handleTest(cred.id)} disabled={testing === cred.id}>
                    {testing === cred.id ? <Loader2 className="w-3 h-3 animate-spin" /> : "测试"}
                  </Button>
                  <Button variant="ghost" size="sm" className="h-7 text-xs text-loss" onClick={() => handleDelete(cred.id)}><Trash2 className="w-3 h-3" /></Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
