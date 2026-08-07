/**
 * AI 一键启动面板
 * 
 * 用户只需选择：账户 + 交易对 + 风险偏好
 * AI 自动完成：分析市场 → 决定风格/周期 → 生成策略 → 激活运行
 */

import { useState, useEffect }from 'react'
import { useTradingPairs } from '@/hooks/useTradingPairs';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Rocket, Loader2, Shield, Zap, TrendingUp,
  Plus, CheckCircle2, AlertTriangle, X, Wallet, ArrowRight
} from 'lucide-react';
import { toast } from 'react-hot-toast';

interface AutoLaunchPanelProps {
  onLaunched?: (strategyId: string) => void;
  onClose?: () => void;
  onSwitchTab?: (tab: string) => void;
}

const RISK_OPTIONS = [
  {
    key: 'conservative',
    label: '保守',
    desc: '低杠杆、逐仓隔离、需手动确认',
    icon: <Shield className="w-5 h-5" />,
    color: 'border-blue-400 bg-blue-50 dark:bg-blue-950/30',
    activeColor: 'border-blue-500 bg-blue-100 dark:bg-blue-900/50 shadow-lg scale-[1.02]',
    params: '杠杆 ≤3x | SL 2% | TP 4%',
    details: '逐仓模式 · 无滚仓 · 最大仓位10%',
    leverage: '1-3x',
    snowball: false,
  },
  {
    key: 'moderate',
    label: '均衡',
    desc: '中等杠杆、逐仓隔离、自动执行',
    icon: <TrendingUp className="w-5 h-5" />,
    color: 'border-green-400 bg-green-50 dark:bg-green-950/30',
    activeColor: 'border-green-500 bg-green-100 dark:bg-green-900/50 shadow-lg scale-[1.02]',
    params: '杠杆 ≤5x | SL 4% | TP 8%',
    details: '逐仓模式 · 盈利5%后可滚仓2次 · 最大仓位20%',
    leverage: '1-5x',
    snowball: true,
  },
  {
    key: 'aggressive',
    label: '激进',
    desc: '高杠杆、极端行情滚仓抓利润',
    icon: <Zap className="w-5 h-5" />,
    color: 'border-orange-400 bg-orange-50 dark:bg-orange-950/30',
    activeColor: 'border-orange-500 bg-orange-100 dark:bg-orange-900/50 shadow-lg scale-[1.02]',
    params: '杠杆 ≤10x | SL 6% | TP 15%',
    details: '逐仓模式 · 盈利3%后可滚仓3次 · 最大仓位35%',
    leverage: '1-10x',
    snowball: true,
  },
];

const FALLBACK_SYMBOLS = ['BTC', 'ETH', 'SOL', 'BNB', 'DOGE', 'XRP', 'ARB', 'OP', 'AVAX', 'ADA'];

export default function AutoLaunchPanel({ onLaunched, onClose, onSwitchTab }: AutoLaunchPanelProps) {
  const { symbols: configuredPairs } = useTradingPairs();
  const POPULAR_SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_SYMBOLS;
  const [accounts, setAccounts] = useState<any[]>([]);
  const [accountId, setAccountId] = useState<number>(0);
  const [symbols, setSymbols] = useState<string[]>(['BTC']);
  const [customInput, setCustomInput] = useState('');
  const [risk, setRisk] = useState('moderate');
  const [tradingMode, setTradingMode] = useState<'paper' | 'live'>('paper');
  const [launching, setLaunching] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [paperBalance, setPaperBalance] = useState<any>(null);

  useEffect(() => {
    fetch('/api/account/list', { signal: AbortSignal.timeout(3000) })
      .then(r => r.ok ? r.json() : [])
      .then(d => {
        setAccounts(d);
        if (d.length > 0) setAccountId(d[0].id);
      })
      .catch(() => {});
  }, []);

  const addSymbol = () => {
    const parts = customInput.trim().toUpperCase().split(/[,，\s]+/).filter(Boolean);
    const existing = new Set(symbols);
    const added: string[] = [];
    for (const s of parts) {
      if (/^[A-Z0-9]{2,10}$/.test(s) && !existing.has(s)) {
        existing.add(s);
        added.push(s);
      }
    }
    if (added.length > 0) setSymbols([...symbols, ...added]);
    setCustomInput('');
  };

  const toggleSymbol = (sym: string) => {
    if (symbols.includes(sym)) {
      if (symbols.length > 1) setSymbols(symbols.filter(s => s !== sym));
    } else {
      setSymbols([...symbols, sym]);
    }
  };

  const handleLaunch = async () => {
    if (!accountId) { toast.error('请选择账户'); return; }
    if (symbols.length === 0) { toast.error('请至少选择一个交易对'); return; }

    setLaunching(true);
    const loadingToast = toast.loading('AI 正在拉取K线数据、计算技术指标、深度分析市场...');

    try {
      const res = await fetch('/api/ai-strategies/auto-launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_id: accountId,
          target_symbols: symbols,
          risk_preference: risk,
          trading_mode: tradingMode,
        }),
        signal: AbortSignal.timeout(60000),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `启动失败 (${res.status})`);
      }

      const data = await res.json();
      setResult(data);

      toast.success(
        `AI自主交易已启动！${data.ai_decided?.style_label || ''} · ${data.ai_decided?.timeframe || ''}`,
        { id: loadingToast, duration: 5000 }
      );

      if (tradingMode === 'paper' && accountId) {
        try {
          const balRes = await fetch(`/api/paper/balance/${accountId}`);
          if (balRes.ok) setPaperBalance(await balRes.json());
        } catch {}
      }

      onLaunched?.(data.strategy_id);
    } catch (e: any) {
      toast.error(e.message || '启动失败', { id: loadingToast });
    } finally {
      setLaunching(false);
    }
  };

  if (result) {
    const d = result.ai_decided || {};
    return (
      <Card className="border-2 border-green-400 dark:border-green-600 overflow-hidden">
        <CardContent className="p-6">
          <div className="text-center space-y-4">
            <div className="w-16 h-16 mx-auto rounded-full bg-green-100 dark:bg-green-900/40 flex items-center justify-center">
              <CheckCircle2 className="w-10 h-10 text-green-500" />
            </div>
            <h3 className="text-xl font-bold text-green-700 dark:text-green-400">
              AI自主交易已启动
              {tradingMode === 'paper' && (
                <span className="ml-2 inline-block px-2 py-0.5 text-xs rounded bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300 align-middle">
                  PAPER
                </span>
              )}
            </h3>
            <p className="text-sm text-muted-foreground">{result.strategy_name}</p>

            {/* 生成方式 + 审核标签 */}
            <div className="flex flex-wrap items-center justify-center gap-2">
              <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium ${
                result.generation_source === 'ai' || result.generation_source === 'adapt_template'
                  ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
                  : result.generation_source === 'use_template' || result.generation_source === 'auto_template'
                  ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300'
                  : result.generation_source === 'data_template'
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                  : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
              }`}>
                {{ ai: 'AI 全新生成', use_template: '模板直接使用', adapt_template: '模板AI适配', auto_template: '自动选用模板', data_template: '数据驱动', template: '预设模板' }[result.generation_source as string] || result.generation_source}
              </span>
              {result.chosen_template_name && (
                <span className="inline-flex items-center px-2 py-1 rounded-full text-[10px] bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                  基于：{result.chosen_template_name}
                </span>
              )}
              {result.candidate_count > 0 && (
                <span className="inline-flex items-center px-2 py-1 rounded-full text-[10px] bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {result.candidate_count} 个候选模板
                </span>
              )}
              {result.signal_count > 0 && (
                <span className="inline-flex items-center px-2 py-1 rounded-full text-[10px] bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300">
                  {result.signal_count} 个交易信号
                </span>
              )}
            </div>

            {/* LLM 审核结果 */}
            {result.audit_decision && (
              <div className="bg-indigo-50 dark:bg-indigo-950/30 rounded-lg p-3 border border-indigo-200 dark:border-indigo-800">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-6 h-6 rounded-full bg-indigo-500 text-white flex items-center justify-center text-[10px] font-bold">
                    {result.audit_score || '?'}
                  </div>
                  <span className="text-xs font-semibold text-indigo-800 dark:text-indigo-300">
                    AI审核评分 {result.audit_score || '-'}/100 — {{
                      use_template: '直接使用模板',
                      adapt_template: '基于模板适配',
                      generate_new: '全新生成策略',
                    }[result.audit_decision as string] || result.audit_decision}
                  </span>
                </div>
                {result.audit_reason && (
                  <p className="text-[11px] text-indigo-700 dark:text-indigo-300 leading-relaxed">
                    {result.audit_reason}
                  </p>
                )}
              </div>
            )}

            {/* AI 决策卡片 */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm bg-green-50 dark:bg-green-950/20 rounded-lg p-4">
              <div>
                <div className="text-xs text-gray-500">AI选择风格</div>
                <div className="font-bold">{d.style_label}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">AI选择周期</div>
                <div className="font-bold">{d.timeframe}</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">市场判断</div>
                <div className="font-bold">{
                  d.market_cycle === 'bull' ? '牛市' :
                  d.market_cycle === 'bear' ? '熊市' :
                  d.market_cycle === 'sideways' ? '震荡' : '分析中'
                }</div>
              </div>
              <div>
                <div className="text-xs text-gray-500">实际杠杆</div>
                <div className="font-bold">{d.default_leverage || 1}x / {d.max_leverage || 3}x</div>
              </div>
            </div>

            {/* 多周期技术分析详情 */}
            {result.analysis_summary?.indicators?.kline_analysis && (
              <div className="text-left bg-slate-50 dark:bg-slate-900/50 rounded-lg p-4 border border-slate-200 dark:border-slate-700">
                <div className="text-xs font-semibold mb-2 text-slate-700 dark:text-slate-300">多周期技术分析（真实K线数据）</div>
                <div className="space-y-2">
                  {Object.entries(result.analysis_summary.indicators.kline_analysis as Record<string, any>).map(([period, data]: [string, any]) => (
                    <div key={period} className="grid grid-cols-7 gap-1 text-[10px] items-center">
                      <div className="font-semibold text-xs col-span-1">
                        {data.label}
                        <span className="text-muted-foreground font-normal ml-0.5">({period})</span>
                      </div>
                      <div className="text-right font-mono">
                        <span className="text-muted-foreground">价格</span> ${data.price?.toLocaleString()}
                      </div>
                      <div className={`text-right font-mono ${(data.change_pct ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {(data.change_pct ?? 0) >= 0 ? '+' : ''}{data.change_pct}%
                      </div>
                      <div className={`text-right font-mono ${
                        data.rsi14 > 70 ? 'text-red-600' : data.rsi14 < 30 ? 'text-green-600' : ''
                      }`}>
                        RSI {data.rsi14}
                      </div>
                      <div className="text-right font-mono text-muted-foreground">
                        ATR {data.atr_pct}%
                      </div>
                      <div className="text-right font-mono">
                        MACD {data.macd > 0 ? '+' : ''}{data.macd}
                      </div>
                      <div className={`text-right font-semibold ${
                        data.trend === 'bullish' ? 'text-green-600' :
                        data.trend === 'bearish' ? 'text-red-600' : 'text-gray-500'
                      }`}>
                        {data.trend === 'bullish' ? '↗ 看多' :
                         data.trend === 'bearish' ? '↘ 看空' : '→ 中性'}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* LLM 缺失警告 */}
            {result.analysis_summary?.llm_warning && (
              <div className="flex items-center gap-2 bg-amber-50 dark:bg-amber-950/30 rounded-lg px-3 py-2 border border-amber-200 dark:border-amber-800">
                <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
                <span className="text-xs text-amber-700 dark:text-amber-300">{result.analysis_summary.llm_warning}</span>
              </div>
            )}

            {d.snowball_enabled && (
              <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                🔥 已启用极端行情滚仓 — 顺势追加仓位，用浮盈做保证金
              </p>
            )}

            <p className="text-xs text-muted-foreground">
              策略已激活并注册到自主分析循环，AI会自动进行短线/中线/长线分析并执行交易
            </p>

            {tradingMode === 'paper' && paperBalance && (
              <div className="bg-amber-50 dark:bg-amber-950/30 rounded-lg p-4 border border-amber-200 dark:border-amber-800">
                <div className="flex items-center gap-2 mb-3">
                  <Wallet className="w-4 h-4 text-amber-600" />
                  <span className="text-sm font-semibold text-amber-800 dark:text-amber-300">模拟资金概览</span>
                </div>
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div className="text-center">
                    <div className="text-[10px] text-muted-foreground">总权益</div>
                    <div className="font-bold text-blue-700 dark:text-blue-400">
                      ${paperBalance.total_equity?.toLocaleString(undefined, { minimumFractionDigits: 2 }) ?? '0.00'}
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] text-muted-foreground">可用余额</div>
                    <div className="font-bold text-green-700 dark:text-green-400">
                      ${paperBalance.available_balance?.toLocaleString(undefined, { minimumFractionDigits: 2 }) ?? '0.00'}
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] text-muted-foreground">初始资金</div>
                    <div className="font-bold">
                      ${paperBalance.initial_balance?.toLocaleString(undefined, { minimumFractionDigits: 2 }) ?? '0.00'}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="flex gap-2 justify-center">
              {tradingMode === 'paper' && onSwitchTab && (
                <Button
                  size="sm"
                  className="bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white"
                  onClick={() => { onSwitchTab('paper-trading'); }}
                >
                  <Wallet className="w-4 h-4 mr-1.5" />
                  查看模拟资金详情
                  <ArrowRight className="w-3.5 h-3.5 ml-1" />
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => { setResult(null); onClose?.(); }}>
                关闭
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-2 border-dashed border-blue-300 dark:border-blue-700 overflow-hidden">
      <CardContent className="p-6 space-y-5">
        {/* 标题 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
              <Rocket className="w-5 h-5 text-white" />
            </div>
            <div>
              <h3 className="text-lg font-bold">一键启动 AI 自主交易</h3>
              <p className="text-xs text-muted-foreground">选好账户和交易对，AI自动决定交易风格、周期和策略</p>
            </div>
          </div>
          {onClose && (
            <Button variant="ghost" size="sm" onClick={onClose}><X className="w-4 h-4" /></Button>
          )}
        </div>

        {/* 账户选择 */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">交易账户</label>
          <select
            className="w-full px-3 py-2 text-sm border rounded-lg bg-background"
            value={accountId || ''}
            onChange={e => setAccountId(parseInt(e.target.value) || 0)}
          >
            <option value="">选择账户</option>
            {accounts.map(a => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </div>

        {/* 交易模式 */}
        <div>
          <label className="text-base font-semibold mb-3 block">交易模式</label>
          <div className="grid grid-cols-2 gap-4">
            <button
              onClick={() => setTradingMode('paper')}
              className={`mode-card ${tradingMode === 'paper' ? 'mode-card-selected border-amber-500 bg-amber-50 dark:bg-amber-950/30' : 'border-border bg-background hover:border-amber-300'}`}
            >
              <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                tradingMode === 'paper' ? 'bg-amber-500 text-white' : 'bg-muted text-muted-foreground'
              }`}>
                <Shield className="w-6 h-6" />
              </div>
              <div>
                <div className="text-base font-semibold">模拟交易</div>
                <div className="text-sm text-muted-foreground">虚拟资金，零风险</div>
              </div>
            </button>
            <button
              onClick={() => setTradingMode('live')}
              className={`mode-card ${tradingMode === 'live' ? 'mode-card-selected border-red-500 bg-red-50 dark:bg-red-950/30' : 'border-border bg-background hover:border-red-300'}`}
            >
              <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                tradingMode === 'live' ? 'bg-red-500 text-white' : 'bg-muted text-muted-foreground'
              }`}>
                <Zap className="w-6 h-6" />
              </div>
              <div>
                <div className="text-base font-semibold">实盘交易</div>
                <div className="text-sm text-muted-foreground">真实资金，需 API</div>
              </div>
            </button>
          </div>
          {tradingMode === 'paper' && (
            <p className="text-sm text-amber-600 dark:text-amber-400 mt-3 flex items-center gap-2">
              <Shield className="w-4 h-4" />
              模拟模式：使用真实行情价格 + 虚拟资金，不调用交易所下单API
            </p>
          )}
          {tradingMode === 'live' && (
            <p className="text-sm text-red-600 dark:text-red-400 mt-3 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              实盘模式：将使用真实资金交易，请确保已配置 API 密钥
            </p>
          )}
        </div>

        {/* 交易对选择 */}
        <div>
          <label className="text-sm font-medium mb-1.5 block">
            交易对
            <span className="text-xs font-normal text-muted-foreground ml-2">已选 {symbols.length} 个，首个为主标的</span>
          </label>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {POPULAR_SYMBOLS.map(sym => {
              const active = symbols.includes(sym);
              return (
                <button
                  key={sym}
                  onClick={() => toggleSymbol(sym)}
                  className={`px-2.5 py-1 text-xs rounded-full border transition-all ${
                    active
                      ? 'bg-blue-100 border-blue-400 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400 font-medium'
                      : 'bg-background border-border text-muted-foreground hover:border-blue-300'
                  }`}
                >
                  {active && '✓ '}{sym}
                </button>
              );
            })}
          </div>
          <div className="flex gap-2">
            <Input
              placeholder="自定义交易对，如 APT, SUI …"
              className="text-sm"
              value={customInput}
              onChange={e => setCustomInput(e.target.value.toUpperCase())}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addSymbol(); } }}
            />
            <Button variant="outline" size="sm" onClick={addSymbol} disabled={!customInput.trim()}>
              <Plus className="w-3.5 h-3.5" />
            </Button>
          </div>
          {/* 已选标签 */}
          {symbols.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {symbols.map((sym, i) => (
                <span key={sym} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                  i === 0 ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                }`}>
                  {i === 0 && '★'}{sym}
                  {symbols.length > 1 && (
                    <button onClick={() => toggleSymbol(sym)} className="text-gray-400 hover:text-red-500 ml-0.5">×</button>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* 风险偏好 */}
        <div>
          <label className="text-sm font-medium mb-2 block">风险偏好 · 杠杆配置</label>
          <div className="grid grid-cols-3 gap-3">
            {RISK_OPTIONS.map(opt => (
              <button
                key={opt.key}
                onClick={() => setRisk(opt.key)}
                className={`p-3 rounded-lg border-2 text-left transition-all ${
                  risk === opt.key ? opt.activeColor : opt.color
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  {opt.icon}
                  <span className="font-bold text-sm">{opt.label}</span>
                </div>
                <p className="text-[10px] text-muted-foreground">{opt.desc}</p>
                <p className="text-[10px] font-mono mt-1.5 font-semibold">{opt.params}</p>
                <p className="text-[9px] text-muted-foreground mt-1">{opt.details}</p>
                {opt.snowball && (
                  <span className="inline-block mt-1.5 px-1.5 py-0.5 text-[9px] rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 font-medium">
                    🔥 极端行情滚仓
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* 风控说明 */}
        <div className="p-3 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
            <div className="text-[10px] text-amber-800 dark:text-amber-300 space-y-1">
              <p className="font-semibold">合约交易风控机制</p>
              <ul className="space-y-0.5 list-disc pl-3">
                <li>杠杆根据市场波动自动降档：极端波动时杠杆减半</li>
                <li>止损严格钳位：确保止损价始终远离爆仓价（≥15%缓冲）</li>
                <li>移动止损：盈利后自动追踪锁定利润，保本止损</li>
                <li>分批止盈：达标后分3次平仓（30%-30%-40%）</li>
                <li>滚仓策略：仅在已盈利且极端趋势确认时追加，用浮盈做保证金</li>
                <li>紧急熔断：距爆仓&lt;5% 或 杠杆亏损&gt;20% 立即全平</li>
              </ul>
            </div>
          </div>
        </div>

        {/* 启动按钮 */}
        <Button
          className={`w-full h-12 text-base font-bold ${
            tradingMode === 'paper'
              ? 'bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600'
              : 'bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700'
          }`}
          onClick={handleLaunch}
          disabled={launching || !accountId}
        >
          {launching ? (
            <>
              <Loader2 className="w-5 h-5 mr-2 animate-spin" />
              拉取K线 → 计算指标 → AI深度分析中...
            </>
          ) : (
            <>
              <Rocket className="w-5 h-5 mr-2" />
              {tradingMode === 'paper' ? '启动模拟交易' : '启动实盘交易'}
            </>
          )}
        </Button>

        <p className="text-[10px] text-center text-muted-foreground">
          {tradingMode === 'paper'
            ? '模拟模式：AI 自动分析 → 生成策略 → 虚拟资金执行，零风险学习和验证'
            : 'AI 将自动分析市场环境 → 选择最优交易风格和周期 → 生成策略 → 激活自主分析循环'
          }
        </p>
      </CardContent>
    </Card>
  );
}
