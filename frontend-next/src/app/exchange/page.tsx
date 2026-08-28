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
  Wallet, Banknote, TrendingUp, Play, StopCircle, Activity,
} from "lucide-react";
import { useAccounts, useCreateAccount, useDeleteAccount, useUpdateAccount, useSessions } from "@/hooks/useTradingData";
import { useDefaultExchange } from "@/hooks/useDefaultExchange";
import { accountApi, sessionApi } from "@/lib/api";
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
  // [2026-08-28 全币安] 新建账户默认交易所跟随后端 settings.DEFAULT_EXCHANGE
  const defaultEx = useDefaultExchange();
  const updateMut = useUpdateAccount();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const [llmConfigs, setLlmConfigs] = useState<any[]>([]);
  const [personalities, setPersonalities] = useState<any[]>([]);
  const [creds, setCreds] = useState<any[]>([]);
  // [2026-08-28 重设计P2] 账户↔会话关联：会话列表 + 卡片内启动会话向导
  const { data: sessions, refetch: refetchSessions } = useSessions();
  const [wizardAcct, setWizardAcct] = useState<any | null>(null);
  const [wz, setWz] = useState({ mode: "paper", paperAccountId: "", symbols: "BTC,ETH,SOL", risk: "moderate" });
  const [wzBusy, setWzBusy] = useState(false);

  const startWizard = async () => {
    if (!wizardAcct) return;
    setWzBusy(true);
    try {
      await sessionApi.start({
        account_id: wizardAcct.id,
        paper_account_id: wz.mode === "paper" ? (parseInt(wz.paperAccountId) || undefined) : undefined,
        symbols: wz.symbols.split(",").map((s: string) => s.trim().toUpperCase()).filter(Boolean),
        trading_mode: wz.mode,
        risk_level: wz.risk,
        active_exchange: wizardAcct.selected_exchange || undefined,
      });
      setWizardAcct(null);
      refetchSessions();
    } catch (e: any) {
      alert(e?.message || String(e));
    } finally {
      setWzBusy(false);
    }
  };
  const accountSessions = (acctId: number) =>
    (sessions || []).filter((s: any) => s.account_id === acctId || s.paper_account_id === acctId);

  useEffect(() => {
    fetch(`${BACKEND}/api/llm-configs`).then(r => r.json()).then(d => setLlmConfigs(d.items || [])).catch(() => {});
    fetch(`${BACKEND}/api/account/personality-presets`).then(r => r.json()).then(setPersonalities).catch(() => {});
    fetch(`${BACKEND}/api/exchange/credentials`).then(r => r.json()).then(d => setCreds(Array.isArray(d) ? d : [])).catch(() => {});
  }, []);

  const [form, setForm] = useState({ name: "", trading_mode: "paper", initial_capital: "500", selected_exchange: defaultEx, llm_config_id: "", llm_config_id_deep: "", personality_id: "", credential_id: "" });

  const handleCreate = async () => {
    if (!form.name.trim()) return;
    const created: any = await createMut.mutateAsync({
      name: form.name.trim(), trading_mode: form.trading_mode,
      account_type: form.trading_mode === "paper" ? "PAPER" : "AI",
      initial_capital: form.trading_mode === "paper" ? (parseFloat(form.initial_capital) || 500) : undefined,
      selected_exchange: form.selected_exchange,
      llm_config_id: form.llm_config_id ? parseInt(form.llm_config_id) : null,
      llm_config_id_deep: form.llm_config_id_deep ? parseInt(form.llm_config_id_deep) : null,
    } as any);
    // [2026-08-28] 实盘账户创建时可顺带绑定 API 凭证
    if (form.trading_mode === "live" && form.credential_id && created?.id) {
      try {
        await fetch(`${BACKEND}/api/exchange/credentials/${form.credential_id}/bind`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_id: created.id }),
        });
      } catch {}
    }
    setForm({ name: "", trading_mode: "paper", initial_capital: "500", selected_exchange: defaultEx, llm_config_id: "", llm_config_id_deep: "", personality_id: "", credential_id: "" });
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
            {form.trading_mode === "paper" ? (
              <div><Label className="text-xs">初始资金</Label><Input type="number" value={form.initial_capital} onChange={(e) => setForm({ ...form, initial_capital: e.target.value })} className="text-sm" /></div>
            ) : (
              <div><Label className="text-xs">实盘资金</Label><div className="text-xs text-muted-foreground pt-1.5">以交易所真实余额为准，无需初始资金</div></div>
            )}
            <div><Label className="text-xs">交易所</Label>
              <select value={form.selected_exchange} onChange={(e) => setForm({ ...form, selected_exchange: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="asterdex">Asterdex</option><option value="hyperliquid">Hyperliquid</option>
                <option value="binance">币安</option><option value="bybit">Bybit</option><option value="okx">OKX</option>
              </select>
            </div>
            {form.trading_mode === "live" && (
              <div><Label className="text-xs">绑定 API 凭证（可选）</Label>
                <select value={form.credential_id} onChange={(e) => setForm({ ...form, credential_id: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                  <option value="">暂不绑定（创建后在编辑里绑定）</option>
                  {(creds || []).map((c: any) => (
                    <option key={c.id} value={c.id}>
                      {EX_NAMES[c.exchange] || c.exchange} · {c.api_key_masked || `#${c.id}`}
                      {c.testnet ? " · 测试网" : " · 主网"}
                      {c.account_id ? ` · 已绑账户#${c.account_id}` : " · 全局"}
                    </option>
                  ))}
                </select>
              </div>
            )}
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

      {/* [重设计P2] 启动会话向导 */}
      {wizardAcct && (
        <Card className="p-4 border-cyan-400/30 space-y-3 glass">
          <div className="flex items-center gap-2">
            <Play className="w-4 h-4 text-cyan-300" />
            <div className="text-sm font-medium">为「{wizardAcct.name}」启动 AI 策略会话</div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div><Label className="text-xs">模式</Label>
              <select value={wz.mode} onChange={(e) => setWz({ ...wz, mode: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="paper">模拟</option><option value="live">实盘</option>
              </select>
            </div>
            {wz.mode === "paper" && (
              <div><Label className="text-xs">模拟资金池</Label>
                <select value={wz.paperAccountId} onChange={(e) => setWz({ ...wz, paperAccountId: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                  <option value="">自动选择</option>
                  {(accounts || []).filter((a: any) => a.trading_mode === "paper").map((a: any) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </div>
            )}
            <div className="col-span-2"><Label className="text-xs">交易对（逗号分隔）</Label>
              <Input value={wz.symbols} onChange={(e) => setWz({ ...wz, symbols: e.target.value })} className="text-sm" />
            </div>
            <div><Label className="text-xs">风险档</Label>
              <select value={wz.risk} onChange={(e) => setWz({ ...wz, risk: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="conservative">保守</option><option value="moderate">均衡</option><option value="aggressive">激进</option>
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <Button size="sm" className="btn-glow" onClick={startWizard} disabled={wzBusy || !wz.symbols.trim()}>
              {wzBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}启动会话
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setWizardAcct(null)}>取消</Button>
          </div>
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
          <LiveGuardBadge />
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
                  {(() => {
                    const ss = accountSessions(a.id);
                    const running = ss.filter((s: any) => s.status === "running");
                    return (
                      <span className="inline-flex items-center gap-1 mr-1 text-[10px] text-muted-foreground">
                        <Activity className="w-3 h-3" />
                        {running.length > 0 ? `${running.length}会话运行中` : `${ss.length}会话`}
                        {running.length > 0 && (
                          <button title="停止该账户全部会话" className="text-loss ml-1"
                            onClick={() => {
                              if (confirm("停止该账户的全部运行中会话？")) {
                                running.forEach((s: any) => sessionApi.stop(s.session_id).catch(() => {}));
                                setTimeout(() => refetchSessions(), 1500);
                              }
                            }}>
                            <StopCircle className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </span>
                    );
                  })()}
                  <button title="启动会话" onClick={() => { setWizardAcct(a); setWz({ mode: a.trading_mode || "paper", paperAccountId: "", symbols: "BTC,ETH,SOL", risk: "moderate" }); }} className="text-cyan-300 hover:text-cyan-200 mr-1"><Play className="w-3.5 h-3.5" /></button>
                  <button onClick={() => setEditing(a)} className="text-primary hover:text-primary/80 mr-1"><Settings2 className="w-3.5 h-3.5" /></button>
                  <button onClick={() => {
                    if (confirm("停用账户？（将自动停止其全部会话，历史保留）。再确认一次可选择彻底删除")) {
                      deleteMut.mutate(a.id);
                    } else if (confirm("彻底删除账户？（仅当无持仓无会话；历史一并清除，不可恢复）")) {
                      accountApi.delete(a.id, { hard: true })
                        .then((r: any) => alert(r?.message || "已彻底删除"))
                        .catch((e: any) => alert(e?.message || String(e)))
                        .finally(() => window.location.reload());
                    }
                  }} className="text-loss hover:text-loss/80"><Trash2 className="w-3.5 h-3.5" /></button>
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
  const defaultExEditor = useDefaultExchange();
  const [form, setForm] = useState({
    name: account.name,
    selected_exchange: account.selected_exchange || defaultExEditor,
    llm_config_id: account.llm_config_id || "",
    llm_config_id_deep: account.llm_config_id_deep || "",
    auto_trading_enabled: account.auto_trading_enabled,
    max_leverage: account.max_leverage || 10,
    default_leverage: account.default_leverage || 10,
    binance_enabled: String(account.binance_enabled || "").toLowerCase() === "true",
    binance_testnet: String(account.binance_testnet || "").toLowerCase() === "true",
    tier_lev: {
      short: account.tier_overrides?.short?.leverage || "",
      mid: account.tier_overrides?.mid?.leverage || "",
      long: account.tier_overrides?.long?.leverage || "",
    },
  });
  const [saving, setSaving] = useState(false);
  const [creds, setCreds] = useState<any[]>([]);
  const [boundCred, setBoundCred] = useState<any | null>(null);
  const defaultCfg = (llmConfigs || []).find((c: any) => c.is_default);

  useEffect(() => {
    fetch(`${BACKEND}/api/exchange/credentials`).then(r => r.json()).then((d) => {
      const list = Array.isArray(d) ? d : [];
      setCreds(list);
      setBoundCred(list.find((c: any) => c.account_id === account.id) || null);
    }).catch(() => {});
  }, [account.id]);

  const handleBindCred = async (credId: string) => {
    try {
      if (boundCred && boundCred.id !== parseInt(credId || "0")) {
        await fetch(`${BACKEND}/api/exchange/credentials/${boundCred.id}/bind`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_id: null }),
        });
      }
      if (credId) {
        await fetch(`${BACKEND}/api/exchange/credentials/${credId}/bind`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_id: account.id }),
        });
      }
      const fresh = await fetch(`${BACKEND}/api/exchange/credentials`).then(r => r.json()).catch(() => []);
      const list = Array.isArray(fresh) ? fresh : [];
      setCreds(list);
      setBoundCred(list.find((c: any) => c.account_id === account.id) || null);
    } catch (e: any) { alert(e?.message || String(e)); }
  };

  const handleSave = async () => {
    setSaving(true);
    await onSave({
      name: form.name,
      selected_exchange: form.selected_exchange,
      llm_config_id: form.llm_config_id ? parseInt(form.llm_config_id) : null,
      llm_config_id_deep: form.llm_config_id_deep ? parseInt(form.llm_config_id_deep) : null,
      auto_trading_enabled: form.auto_trading_enabled,
      max_leverage: parseFloat(String(form.max_leverage)) || 10,
      default_leverage: parseFloat(String(form.default_leverage)) || 10,
      binance_enabled: form.binance_enabled,
      binance_testnet: form.binance_testnet,
    });
    const to: any = {};
    (["short", "mid", "long"] as const).forEach((t) => {
      const v = parseFloat(String(form.tier_lev[t]));
      if (!Number.isNaN(v) && v > 0) to[t] = { leverage: v };
    });
    try {
      await fetch(`${BACKEND}/api/unified-account/${account.id}/tier-overrides`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier_overrides: to }),
      });
    } catch {}
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
        <div><Label className="text-xs">最大杠杆</Label>
          <Input type="number" value={form.max_leverage} onChange={(e) => setForm({ ...form, max_leverage: e.target.value })} className="text-sm" />
        </div>
        <div><Label className="text-xs">默认杠杆</Label>
          <Input type="number" value={form.default_leverage} onChange={(e) => setForm({ ...form, default_leverage: e.target.value })} className="text-sm" />
        </div>
        <div><Label className="text-xs">币安实盘</Label>
          <div className="flex items-center gap-2 pt-1">
            <button onClick={() => setForm({ ...form, binance_enabled: !form.binance_enabled })}
              className={cn("relative w-11 h-6 rounded-full transition-colors", form.binance_enabled ? "bg-primary" : "bg-muted")}>
              <span className={cn("absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform", form.binance_enabled ? "left-5" : "left-0.5")} />
            </button>
            <span className="text-xs">{form.binance_enabled ? "已启用" : "未启用"}</span>
          </div>
        </div>
        <div><Label className="text-xs">币安测试网</Label>
          <div className="flex items-center gap-2 pt-1">
            <button onClick={() => setForm({ ...form, binance_testnet: !form.binance_testnet })}
              className={cn("relative w-11 h-6 rounded-full transition-colors", form.binance_testnet ? "bg-primary" : "bg-muted")}>
              <span className={cn("absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform", form.binance_testnet ? "left-5" : "left-0.5")} />
            </button>
            <span className="text-xs">{form.binance_testnet ? "测试网" : "主网"}</span>
          </div>
        </div>
      </div>
      <div className="border-t border-border/40 pt-2">
        <div className="text-xs font-medium mb-1">三周期杠杆覆盖（留空=跟随全局模板）</div>
        <div className="grid grid-cols-3 gap-2">
          {(["short", "mid", "long"] as const).map((t) => (
            <div key={t}>
              <Label className="text-xs">{t === "short" ? "短线" : t === "mid" ? "中线" : "长线"}</Label>
              <Input type="number" placeholder="如 10" value={form.tier_lev[t]}
                onChange={(e) => setForm({ ...form, tier_lev: { ...form.tier_lev, [t]: e.target.value } })}
                className="text-sm" />
            </div>
          ))}
        </div>
      </div>
      <div className="border-t border-border/40 pt-2 space-y-2">
        <div className="text-xs font-medium">交易所 API 凭证（实盘执行按账户绑定凭证优先）</div>
        {String(account.trading_mode) === "live" && !boundCred && (
          <div className="text-[11px] text-warning flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />实盘账户未绑定 API 凭证，实盘下单将回退全局凭证（可能失败）
          </div>
        )}
        <div className="flex items-center gap-2 flex-wrap">
          <select value={boundCred?.id || ""} onChange={(e) => handleBindCred(e.target.value)}
            className="bg-card border border-border text-sm rounded px-2 py-1.5">
            <option value="">不绑定（使用全局凭证）</option>
            {(creds || []).map((c: any) => (
              <option key={c.id} value={c.id}>
                {EX_NAMES[c.exchange] || c.exchange} · {c.api_key_masked || `#${c.id}`}
                {c.testnet ? " · 测试网" : " · 主网"}
                {c.account_id && c.account_id !== account.id ? ` · 已绑账户#${c.account_id}` : ""}
              </option>
            ))}
          </select>
          {boundCred && (
            <span className="text-[11px] text-muted-foreground">
              当前绑定: {EX_NAMES[boundCred.exchange] || boundCred.exchange} {boundCred.api_key_masked || ""}（{boundCred.testnet ? "测试网" : "主网"}{boundCred.proxy_url ? ` · 代理:${boundCred.proxy_url}` : ""}）
            </span>
          )}
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
        try { balResults[s.exchange] = await fetch(`${BACKEND}/api/exchange/${s.exchange}/balance`).then(r => r.json()); } catch {}
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
  const { data: accounts } = useAccounts();
  const [credentials, setCredentials] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ exchange: "binance", api_key: "", api_secret: "", passphrase: "", label: "", account_id: "", testnet: false, proxy_url: "" });
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
        body: JSON.stringify({ ...form, account_id: form.account_id ? parseInt(form.account_id) : null, proxy_url: (form.proxy_url || "").trim() || null }),
      });
      setShowAdd(false);
      setForm({ exchange: "binance", api_key: "", api_secret: "", passphrase: "", label: "", account_id: "", testnet: false, proxy_url: "" });
      load();
    } catch (e: any) { alert(e.message); }
    setSaving(false);
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    try {
      const result = await fetch(`${BACKEND}/api/exchange/credentials/${id}/test`, { method: "POST" }).then(r => r.json());
      alert(result.connected ? "✅ 连接成功" : `❌ ${result.error || "连接失败"}`);
    } catch (e: any) { alert(e.message); }
    setTesting(null);
  };

  const handleDelete = async (id: number) => {
    if (!confirm("确认删除此凭证？")) return;
    try { await fetch(`${BACKEND}/api/exchange/credentials/${id}`, { method: "DELETE" }); load(); }
    catch (e: any) { alert(e.message); }
  };

  const handleBind = async (id: number, accountId: string) => {
    try {
      await fetch(`${BACKEND}/api/exchange/credentials/${id}/bind`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: accountId ? parseInt(accountId) : null }),
      });
      load();
    } catch (e: any) { alert(e?.message || String(e)); }
  };

  const acctName = (id: number | null | undefined) => {
    if (!id) return null;
    const a = (accounts || []).find((x: any) => x.id === id);
    return a ? a.name : `账户#${id}`;
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
            <div><Label className="text-xs">绑定账户（可选）</Label>
              <select value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="">全局凭证（不绑定）</option>
                {(accounts || []).map((a: any) => <option key={a.id} value={a.id}>{a.name}（{a.trading_mode === "live" ? "实盘" : "模拟"}）</option>)}
              </select>
            </div>
            <div><Label className="text-xs">网络</Label>
              <select value={form.testnet ? "testnet" : "mainnet"} onChange={(e) => setForm({ ...form, testnet: e.target.value === "testnet" })} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
                <option value="mainnet">主网（真实交易）</option>
                <option value="testnet">测试网</option>
              </select>
            </div>
            <div className="col-span-2"><Label className="text-xs">代理地址（可选）</Label>
              <Input value={form.proxy_url} onChange={(e) => setForm({ ...form, proxy_url: e.target.value })} placeholder="留空=用后端全局代理；如 http://127.0.0.1:7890 或 socks5://1.2.3.4:1080" className="text-sm font-mono" />
              <div className="text-[10px] text-muted-foreground mt-1">
                币安 API 需 IP 白名单：请确保本系统出口 IP（或上方代理的出口 IP）已在币安后台加入白名单，否则请求返回 -2015 错误。
              </div>
            </div>
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
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded bg-primary/10 flex items-center justify-center">
                    <Key className="w-4 h-4 text-primary" />
                  </div>
                  <div>
                    <div className="text-sm font-medium flex items-center gap-1.5">
                      {EX_NAMES[cred.exchange] || cred.exchange} {cred.label && `· ${cred.label}`}
                      <Badge variant="secondary" className="text-[10px]">{cred.testnet ? "测试网" : "主网"}</Badge>
                    </div>
                    <div className="text-xs text-muted-foreground font-mono">
                      {cred.api_key_masked || (cred.has_key ? "已配置" : "未配置密钥")}
                      {cred.proxy_url && ` · 代理: ${cred.proxy_url}`}
                      {acctName(cred.account_id) && ` · 绑定: ${acctName(cred.account_id)}`}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <select value={cred.account_id || ""} onChange={(e) => handleBind(cred.id, e.target.value)}
                    className="bg-card border border-border text-xs rounded px-1.5 py-1">
                    <option value="">全局</option>
                    {(accounts || []).map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
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


// ═══ [重设计P3] 实盘执行健康灯 ═══
function LiveGuardBadge() {
  const [st, setSt] = useState<any>(null);
  useEffect(() => {
    fetch(`${BACKEND}/api/unified-account/live-guard-status`)
      .then((r) => r.json()).then(setSt).catch(() => {});
  }, []);
  if (!st) return null;
  return (
    <div className="flex items-center gap-2 text-[10px] text-muted-foreground flex-shrink-0">
      <span className={cn("flex items-center gap-1 px-1.5 py-0.5 rounded", st.sub_position_tracking ? "bg-profit/10 text-profit" : "bg-muted text-muted-foreground")}>
        <Activity className="w-3 h-3" />LPM合并账本 {st.sub_position_tracking ? "ON" : "OFF"}
      </span>
      <span className={cn("flex items-center gap-1 px-1.5 py-0.5 rounded", st.reconcile_enabled ? "bg-profit/10 text-profit" : "bg-muted text-muted-foreground")}>
        对账 {st.reconcile_enabled ? "ON" : "OFF"}
      </span>
      {st.live_accounts > 0 && (
        <span className="px-1.5 py-0.5 rounded bg-cyan-400/10 text-cyan-300">实盘账户 {st.live_accounts} · 子仓 {st.sub_positions}</span>
      )}
    </div>
  );
}
