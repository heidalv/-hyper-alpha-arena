import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import {
  Rocket, Square, Pause, Play, RefreshCw, Shield, TrendingUp,
  TrendingDown, AlertTriangle, CheckCircle2, Clock, Zap, Bot,
  Activity, Target, BarChart3, CircleDot, Check, Eye, ChevronRight, ChevronDown, ChevronUp, Plus, X,
  Brain, Sparkles, Search, Cpu, ArrowLeftRight,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { fmtTime, parseUTC } from '@/lib/utils';
import { useTradingPairs } from '@/hooks/useTradingPairs';
import { MidLongThesisPanel } from './MidLongThesisPanel';

interface SessionInfo {
  session_id: string;
  account_id: number;
  paper_account_id?: number | null;
  trading_account_id?: number | null;
  status: string;
  symbols: string[];
  risk_level: string;
  risk_mode: string;
  trading_mode: string;
  auto_coin_enabled?: boolean;
  auto_coin_symbols?: string[];
  started_at: string | null;
  stopped_at: string | null;
  total_strategies_created: number;
  active_strategies: Array<{
    strategy_id: string;
    name: string;
    status: string;
    primary_symbol: string;
    timeframe: string;
    timeframe_tier?: string | null;
    total_trades: number;
    win_rate: number;
    total_pnl: number;
  }>;
  terminated_count: number;
  total_pnl: number;
  total_trades: number;
  winning_trades: number;
  win_rate: number;
  max_drawdown: number;
  current_drawdown: number;
  last_health_check: string | null;
  last_market_summary: Record<string, any> | null;
  recent_events: Array<{ time: string; event: string; detail: string; severity?: string }>;
  system_health?: {
    data_flow_ok: boolean;
    ai_connection_ok: boolean;
    consecutive_ai_failures: number;
    last_ai_success: string | null;
    active_alerts: Array<{ time: string; event: string; detail: string; severity: string }>;
  };
  config: Record<string, any>;
  current_risk_assessment?: {
    effective_level: string;
    reason: string;
    market_volatility: string;
    adjusted_at: string;
  };
  analyst_reports?: Record<string, {
    analyst: string;
    risk_score: number;
    summary: string;
    recommendation: string;
    signals: Array<{ symbol: string; signal: string; score: number; detail: string; data: Record<string, any> }>;
  }>;
  trader_mental_state?: {
    state: string;
    description: string;
    hint: string;
    block_reason?: string;
    blocks_new_opens: boolean;
    consecutive_losses: number;
    consecutive_wins: number;
    daily_pnl: number;
    daily_trades: number;
    cooldown_until: string | null;
    cooldown_remaining_min: number;
    size_multiplier: number;
    leverage_cap: number;
    high_conf_bypass_threshold: number;
  } | null;
}

interface SessionListItem {
  session_id: string;
  account_id: number;
  account_name?: string;
  paper_account_id?: number | null;
  paper_account_name?: string | null;
  trading_account_id?: number | null;
  status: string;
  symbols: string[];
  risk_level: string;
  trading_mode: string;
  total_strategies_created: number;
  active_count: number;
  total_pnl: number;
  total_trades: number;
  win_rate: number;
  started_at: string | null;
}

interface AutoCoinStatus {
  session_id: string;
  running: boolean;
  exchange: string | null;
  segment: string | null;
  last_scan_at: string | null;
  last_injected_symbols: string[];
  auto_symbols: string[];
  candidate_pool: {
    active: Record<string, { score: number; ai_confidence: number; injected_at: string | null }>;
    cooling_count: number;
    blacklist_count: number;
    max_active: number;
    cooling_period: number;
  } | null;
  error: string | null;
  next_scan_in?: number;
}

const FALLBACK_SYMBOLS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ARB', 'OP', 'AVAX', 'LINK', 'SUI'];

interface FullAutoPanelProps {
  onNavigateToStrategy?: (strategyId: string) => void;
}

const RISK_MODE_OPTIONS = [
  {
    value: 'ai_dynamic',
    label: 'AI 动态',
    desc: 'AI 根据市场波动率、趋势强度自动调整风控参数',
    color: 'text-violet-600',
    icon: Brain,
  },
  {
    value: 'conservative',
    label: '偏保守',
    desc: 'AI 动态调整，但偏向保守上限',
    color: 'text-blue-600',
    icon: Shield,
  },
  {
    value: 'aggressive',
    label: '偏激进',
    desc: 'AI 动态调整，但放宽激进下限',
    color: 'text-red-600',
    icon: Zap,
  },
];

export default function FullAutoPanel({ onNavigateToStrategy }: FullAutoPanelProps = {}) {
  const { symbols: configuredPairs } = useTradingPairs();
  const POPULAR_SYMBOLS = configuredPairs.length > 0 ? configuredPairs : FALLBACK_SYMBOLS;

  const [accounts, setAccounts] = useState<Array<{
    id: number;
    name: string;
    trading_mode: string;
    account_type?: string;
    is_active?: boolean | string;
  }>>([]);
  const [selectedAccount, setSelectedAccount] = useState<number | null>(null);
  const [selectedPaperAccount, setSelectedPaperAccount] = useState<number | null>(null);
  // 会话级交易所覆盖：null=跟随账户配置(account.selected_exchange)
  const [activeExchange, setActiveExchange] = useState<string | null>(null);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['BTC']);
  const [customSymbolInput, setCustomSymbolInput] = useState('');
  const [riskMode, setRiskMode] = useState('ai_dynamic');
  const [tradingMode, setTradingMode] = useState('paper');
  const [autoCoinEnabled, setAutoCoinEnabled] = useState(true);
  const [arbEnabled, setArbEnabled] = useState(false);
  const [arbitrageProfile, setArbitrageProfile] = useState<any | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [showStartPanel, setShowStartPanel] = useState(false);

  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<SessionInfo | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [sessionDetails, setSessionDetails] = useState<Map<string, SessionInfo>>(new Map());
  const [showAddSymbols, setShowAddSymbols] = useState(false);
  const [symbolsToAdd, setSymbolsToAdd] = useState<string[]>([]);
  const [customAddInput, setCustomAddInput] = useState('');
  const [addingSymbols, setAddingSymbols] = useState(false);
  const [removingSymbol, setRemovingSymbol] = useState<string | null>(null);
  const [mergingDup, setMergingDup] = useState(false);
  const [strategyListCollapsed, setStrategyListCollapsed] = useState(true);
  const [autoCoinScanning, setAutoCoinScanning] = useState(false);
  const [autoCoinStatus, setAutoCoinStatus] = useState<AutoCoinStatus | null>(null);
  const [autoCoinStatusLoaded, setAutoCoinStatusLoaded] = useState(false);
  const [autoCoinCountdown, setAutoCoinCountdown] = useState<number | null>(null);

  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState(false);

  const loadAccounts = useCallback(async (retries = 2) => {
    setAccountsError(false);
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await fetch('/api/account/list');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.accounts || [];
        setAccounts(list);
        setAccountsLoading(false);
        setSelectedAccount(prev => {
          if (prev != null) return prev;
          const firstTrader = list.find((a: any) => a.trading_mode !== 'paper');
          return firstTrader ? firstTrader.id : (list.length > 0 ? list[0].id : null);
        });
        setSelectedPaperAccount(prev => {
          if (prev != null) return prev;
          const paperCandidates = list.filter(
            (a: any) =>
              (a.account_type || '').toUpperCase() === 'PAPER' ||
              a.trading_mode === 'paper'
          );
          const activePaper = paperCandidates.filter(
            (a: any) => a.is_active === true || a.is_active === 'true'
          );
          const pick = (activePaper.length > 0 ? activePaper : paperCandidates)
            .slice()
            .sort((a: any, b: any) => b.id - a.id)[0];
          return pick ? pick.id : null;
        });
        return;
      } catch {
        if (attempt < retries) {
          await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
        }
      }
    }
    setAccountsLoading(false);
    setAccountsError(true);
  }, []);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (showStartPanel && (accounts.length === 0 || accountsError)) {
      loadAccounts();
    }
  }, [showStartPanel, accounts.length, accountsError, loadAccounts]);

  const paperAccounts = accounts.filter(
    a => (a.account_type || '').toUpperCase() === 'PAPER' || a.trading_mode === 'paper'
  );
  const traderAccounts = accounts.filter(
    a => (a.account_type || '').toUpperCase() !== 'PAPER' && a.trading_mode !== 'paper'
  );

  useEffect(() => {
    let cancelled = false;
    async function loadProfile() {
      if (!selectedAccount) {
        setArbitrageProfile(null);
        return;
      }
      setProfileLoading(true);
      try {
        const res = await fetch(`/api/accounts/${selectedAccount}/arbitrage-profile`);
        if (res.ok) {
          const profile = await res.json();
          if (!cancelled) {
            setArbitrageProfile(profile);
            if (profile.enabled) {
              setArbEnabled(true);
              if (profile.mode === 'paper' && profile.paper_account_mode !== 'dedicated_arbitrage_paper' && profile.paper_account_id) {
                setTradingMode('paper');
                setSelectedPaperAccount(profile.paper_account_id);
              } else if (profile.mode === 'paper' && profile.paper_account_mode === 'dedicated_arbitrage_paper') {
                setTradingMode('paper');
              }
            }
          }
        }
      } catch {
        if (!cancelled) setArbitrageProfile(null);
      } finally {
        if (!cancelled) setProfileLoading(false);
      }
    }
    loadProfile();
    return () => { cancelled = true; };
  }, [selectedAccount]);

  const showStartPanelRef = useRef(showStartPanel);
  showStartPanelRef.current = showStartPanel;

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/full-auto/sessions');
      if (res.ok) {
        const data = await res.json();
        setSessions(data);
        // Auto-select first running session if none selected (skip when start panel is open)
        if (!showStartPanelRef.current) {
          const running = data.find((s: SessionListItem) => ['running', 'defensive', 'paused'].includes(s.status));
          if (running && !activeSessionId) {
            setActiveSessionId(running.session_id);
            loadSessionDetail(running.session_id);
          }
        }
        // If selected session is gone, clear it
        if (activeSessionId && !data.find((s: SessionListItem) => s.session_id === activeSessionId)) {
          setActiveSessionId(null);
          setActiveSession(null);
        }
      }
    } catch {}
  }, [activeSessionId]);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = activeSessionId;

  // Poll session list every 6s, and active session detail every 3s
  useEffect(() => {
    const listTimer = setInterval(() => {
      if (document.visibilityState === 'visible') loadSessions();
    }, 6000);
    return () => clearInterval(listTimer);
  }, [loadSessions]);

  useEffect(() => {
    if (!activeSessionId) return;
    const sid = activeSessionId;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') loadSessionDetail(sid);
    }, 3000);
    return () => clearInterval(timer);
  }, [activeSessionId]);

  const loadAutoCoinStatus = useCallback(async (sid: string) => {
    try {
      const res = await fetch(`/api/auto-coin/${sid}/status`);
      if (res.ok) {
        const raw = await res.json();
        if (raw.last_scan_at) {
          const elapsed = (Date.now() - new Date(raw.last_scan_at).getTime()) / 1000;
          raw.next_scan_in = Math.max(0, Math.round(3600 - elapsed));
        }
        // Normalize: ensure array/object fields are never null
        raw.auto_symbols = raw.auto_symbols || [];
        raw.last_injected_symbols = raw.last_injected_symbols || [];
        raw.candidate_pool = raw.candidate_pool || { active: {}, max_active: 5, cooling_count: 0, blacklist_count: 0 };
        setAutoCoinStatus(raw);
      } else {
        setAutoCoinStatus(null);
      }
    } catch {
      setAutoCoinStatus(null);
    } finally {
      setAutoCoinStatusLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!activeSessionId) {
      setAutoCoinStatus(null);
      setAutoCoinStatusLoaded(false);
      setAutoCoinCountdown(null);
      return;
    }
    setAutoCoinStatusLoaded(false);
    const sid = activeSessionId;
    loadAutoCoinStatus(sid);
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') loadAutoCoinStatus(sid);
    }, 15000);
    return () => clearInterval(timer);
  }, [activeSessionId, loadAutoCoinStatus]);

  useEffect(() => {
    if (!autoCoinStatus?.next_scan_in) {
      setAutoCoinCountdown(null);
      return;
    }
    setAutoCoinCountdown(autoCoinStatus.next_scan_in);
    const ticker = setInterval(() => {
      setAutoCoinCountdown(prev => (prev != null && prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(ticker);
  }, [autoCoinStatus?.next_scan_in]);

  const addCustomSymbol = (input: string, setter: (s: string) => void, list: string[], listSetter: (fn: (prev: string[]) => string[]) => void) => {
    const sym = input.trim().toUpperCase();
    if (!sym || !/^[A-Z0-9]{1,20}$/.test(sym)) return;
    if (list.includes(sym)) { setter(''); return; }
    listSetter(prev => [...prev, sym]);
    setter('');
  };

  const mergeDuplicateStrategies = async () => {
    if (!activeSession?.session_id) return;
    if (!confirm(
      'Each symbol will retain one strategy per tier (short/mid/long), prioritizing active ones with more trades. '
      + 'Duplicates will be archived and removed from the session list.\n\nProceed?'
    )) return;
    setMergingDup(true);
    try {
      const res = await fetch(
        `/api/full-auto/merge-duplicates/${activeSession.session_id}`,
        { method: 'POST' },
      );
      let data: Record<string, unknown> = {};
      try {
        data = await res.json();
      } catch {
        data = {};
      }
      if (!res.ok) {
        const d = data.detail;
        const msg = typeof d === 'string'
          ? d
          : Array.isArray(d) && d[0] && typeof (d[0] as { msg?: string }).msg === 'string'
            ? (d[0] as { msg: string }).msg
            : '合并失败';
        alert(msg);
        return;
      }
      const cnt = Number(data.merged_symbol_count || 0);
      const rem = Number(data.removed_strategy_count || 0);
      alert(
        cnt > 0
          ? `已处理 ${cnt} 个币种，移除 ${rem} 条重复策略实例。`
          : '当前没有同币种重复策略，无需合并。',
      );
      await loadSessionDetail(activeSession.session_id);
    } finally {
      setMergingDup(false);
    }
  };

  const loadSessionDetail = async (sid: string) => {
    try {
      const res = await fetch(`/api/full-auto/status/${sid}`);
      if (res.ok) {
        const raw = await res.json();
        // Normalize: ensure array fields are never null
        const data = {
          ...raw,
          active_strategies: raw.active_strategies || [],
          symbols: raw.symbols || [],
          auto_coin_symbols: raw.auto_coin_symbols || [],
          recent_events: raw.recent_events || [],
        };
        setActiveSession((prev) => {
          if (!prev || prev.session_id !== data.session_id) return data;
          return { ...prev, ...data };
        });
        if (typeof data.auto_coin_enabled === 'boolean') {
          setAutoCoinEnabled(data.auto_coin_enabled);
        }
      } else {
        console.warn(`[FullAuto] status/${sid} returned ${res.status}, falling back to session list`);
        const listItem = sessions.find(s => s.session_id === sid);
        if (listItem && ['running', 'defensive', 'paused'].includes(listItem.status) && !activeSession) {
          setActiveSession({
            session_id: listItem.session_id,
            account_id: listItem.account_id,
            status: listItem.status,
            symbols: listItem.symbols || [],
            risk_level: listItem.risk_level,
            trading_mode: listItem.trading_mode,
            started_at: listItem.started_at,
            stopped_at: null,
            total_strategies_created: listItem.total_strategies_created || 0,
            active_strategies: [],
            terminated_count: 0,
            total_pnl: listItem.total_pnl || 0,
            total_trades: listItem.total_trades || 0,
            winning_trades: 0,
            win_rate: listItem.win_rate || 0,
            max_drawdown: 0,
            current_drawdown: 0,
            last_health_check: null,
            last_market_summary: null,
            recent_events: [],
            config: {},
          });
        }
      }
    } catch (e) {
      console.warn('[FullAuto] loadSessionDetail error:', e);
    }
  };

  const handleStart = async () => {
    if (selectedAccount == null || selectedSymbols.length === 0) return;
    const usesDedicatedArbPaper = Boolean(
      arbEnabled
      && arbitrageProfile?.paper_account_mode === 'dedicated_arbitrage_paper'
      && arbitrageProfile?.arbitrage_paper_account_id
    );
    if (tradingMode === 'paper' && !usesDedicatedArbPaper && (selectedPaperAccount == null || selectedPaperAccount <= 0)) {
      toast.error('请先选择「模拟账户(资金池)」—— 这是真实下单的账户，不能为空');
      return;
    }
    setStarting(true);
    try {
      const res = await fetch('/api/full-auto/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_id: selectedAccount,
          paper_account_id: tradingMode === 'paper' && !usesDedicatedArbPaper ? selectedPaperAccount : null,
          symbols: selectedSymbols,
          risk_level: riskMode === 'ai_dynamic' ? 'moderate' : riskMode,
          risk_mode: riskMode,
          trading_mode: tradingMode,
          auto_coin_enabled: autoCoinEnabled,
          arb_enabled: arbEnabled,
          arbitrage_profile_id: arbEnabled && arbitrageProfile?.id ? arbitrageProfile.id : null,
          paper_account_mode: usesDedicatedArbPaper ? 'dedicated_arbitrage_paper' : 'legacy_ai_paper',
          arbitrage_paper_account_id: usesDedicatedArbPaper ? arbitrageProfile.arbitrage_paper_account_id : null,
          active_exchange: activeExchange || null,
        }),
      });
      const data = await res.json();
      if (data.success) {
        const paperLabel = data.paper_account_name
          ? `${data.paper_account_name} (#${data.paper_account_id})`
          : (data.trading_account_id ? `#${data.trading_account_id}` : '');
        if (data.arbitrage_paper_account_id) {
          toast.success(`会话已启动，套利专用 Paper 账户锁定: #${data.arbitrage_paper_account_id}`);
        } else if (tradingMode === 'paper' && paperLabel) {
          toast.success(`会话已启动，资金池锁定: ${paperLabel}`);
        }
        setShowStartPanel(false);
        await loadSessions();
        if (data.session_id) {
          setActiveSessionId(data.session_id);
          loadSessionDetail(data.session_id);
        }
      } else if (data.session_id) {
        toast.error(data.error || '该交易员已有运行中的会话，无法重复绑定另一个模拟账户');
        setActiveSessionId(data.session_id);
        loadSessionDetail(data.session_id);
        await loadSessions();
      } else {
        toast.error(data.error || '启动失败');
      }
    } catch (e: any) {
      alert('启动失败: ' + e.message);
    } finally {
      setStarting(false);
    }
  };

  const handleAction = async (action: string) => {
    if (!activeSession) return;
    setRefreshing(true);
    try {
      const res = await fetch(`/api/full-auto/${action}/${activeSession.session_id}`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `操作失败 (HTTP ${res.status})` }));
        toast.error(err.detail || err.error || '操作失败');
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (data.success === false) {
        toast.error(data.error || '操作失败');
        return;
      }
      toast.success(
        action === 'stop' ? '全自动交易已停止' :
        action === 'pause' ? '已暂停' : '已恢复'
      );
      await loadSessions();
    } catch {
      toast.error('网络错误');
    } finally {
      setRefreshing(false);
    }
  };

  const handleDelete = async (sessionId: string) => {
    if (!confirm('确定删除此会话？运行中的会话将先被停止。')) return;
    try {
      const res = await fetch(`/api/full-auto/${sessionId}`, { method: 'DELETE' });
      if (res.ok) {
        if (activeSessionId === sessionId) {
          setActiveSessionId(null);
          setActiveSession(null);
        }
        await loadSessions();
      } else {
        const err = await res.json().catch(() => ({ detail: '删除失败' }));
        toast.error(err.detail || '删除失败');
      }
    } catch (e: any) {
      toast.error('删除失败: ' + e.message);
    }
  };

  const toggleSymbol = (sym: string) => {
    setSelectedSymbols(prev =>
      prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]
    );
  };

  const toggleSymbolToAdd = (sym: string) => {
    setSymbolsToAdd(prev =>
      prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]
    );
  };

  const handleAddSymbols = async () => {
    if (!activeSession || symbolsToAdd.length === 0) return;
    setAddingSymbols(true);
    try {
      const res = await fetch(`/api/full-auto/add-symbols/${activeSession.session_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: symbolsToAdd }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        toast.error(data.detail || data.error || '添加失败');
        return;
      }
      const added = (data.added?.length ? data.added : symbolsToAdd) as string[];
      toast.success(`已添加: ${added.join(', ')}`);
      setShowAddSymbols(false);
      setSymbolsToAdd([]);
      setActiveSession(prev => (
        prev && prev.session_id === activeSession.session_id
          ? { ...prev, symbols: data.symbols || [...(prev.symbols || []), ...added] }
          : prev
      ));
      void loadSessionDetail(activeSession.session_id);
      void fetch(`/api/full-auto/health-check/${activeSession.session_id}`, { method: 'POST' }).catch(() => {});
    } catch (e: any) {
      toast.error('添加失败: ' + (e.message || '网络错误'));
    } finally {
      setAddingSymbols(false);
    }
  };

  const handleRemoveSymbol = async (sym: string) => {
    if (!activeSession) return;
    if (activeSession.symbols.length <= 1) {
      alert('至少保留一个交易对');
      return;
    }
    if (!confirm(`确定移除 ${sym}/USDT？\n该交易对下的策略将被暂停。`)) return;
    setRemovingSymbol(sym);
    try {
      const res = await fetch(`/api/full-auto/remove-symbols/${activeSession.session_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: [sym] }),
      });
      const data = await res.json();
      if (data.success || res.ok) {
        await loadSessionDetail(activeSession.session_id);
      } else {
        alert(data.detail || data.error || '移除失败');
      }
    } catch (e: any) {
      alert('移除失败: ' + e.message);
    } finally {
      setRemovingSymbol(null);
    }
  };

  const handleScanNow = async (sessionId: string) => {
    setAutoCoinScanning(true);
    try {
      const res = await fetch(`/api/auto-coin/${sessionId}/scan-now`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        const injected = data.cycle_result?.phases?.inject?.injected ?? data.cycle_result?.injected_count ?? 0;
        const blocked = data.cycle_result?.phases?.inject?.blocked_reason;
        if (blocked) {
          toast.error(blocked);
        } else {
          toast.success(`扫描完成，注入 ${injected} 个币种`);
        }
        await loadSessionDetail(sessionId);
        await loadAutoCoinStatus(sessionId);
      } else {
        toast.error(data.error || data.detail || '扫描失败');
      }
    } catch (e: any) {
      toast.error('扫描请求失败: ' + e.message);
    } finally {
      setAutoCoinScanning(false);
    }
  };

  const handleToggleAutoCoin = async (sessionId: string, enable: boolean) => {
    setAutoCoinScanning(true);
    try {
      const res = await fetch(`/api/auto-coin/${sessionId}/${enable ? 'start' : 'stop'}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        toast.error(data.detail || data.error || `${enable ? '开启' : '关闭'}自动选币失败`);
        return;
      }
      toast.success(enable ? 'AI 自动选币已开启，正在执行首次扫描...' : 'AI 自动选币已关闭');
      setAutoCoinEnabled(enable);
      await loadSessionDetail(sessionId);
      await loadAutoCoinStatus(sessionId);
      if (enable) {
        const scanRes = await fetch(`/api/auto-coin/${sessionId}/scan-now`, { method: 'POST' });
        const scanData = await scanRes.json();
        if (scanData.success) {
          const injected = scanData.cycle_result?.phases?.inject?.injected ?? scanData.cycle_result?.injected_count ?? 0;
          const blocked = scanData.cycle_result?.phases?.inject?.blocked_reason;
          if (blocked) {
            toast.error(blocked);
          } else {
            toast.success(`首次扫描完成，注入 ${injected} 个币种`);
          }
          await loadSessionDetail(sessionId);
          await loadAutoCoinStatus(sessionId);
        } else {
          toast.error(scanData.error || scanData.detail || '首次扫描失败，将在约 1 小时后自动重试');
        }
      }
    } catch (e: any) {
      toast.error(`${enable ? '开启' : '关闭'}失败: ${e.message}`);
    } finally {
      setAutoCoinScanning(false);
    }
  };

  // ── 策略按币种分组（缓存） ──
  const strategyGrouped = useMemo(() => {
    const grouped: Record<string, NonNullable<SessionInfo['active_strategies']>> = {};
    (activeSession?.active_strategies || []).forEach(st => {
      const sym = st.primary_symbol || 'OTHER';
      if (!grouped[sym]) grouped[sym] = [];
      grouped[sym].push(st);
    });
    return grouped;
  }, [activeSession?.active_strategies]);

  // ── 账户历史 session 按"实盘 / 模拟"分组汇总 ──
  // 数据源：loadSessions() 拉回来的 sessions 列表（最近 20 条）。
  // 目的：在"运行控制"页同时看到账户下 实盘累计 vs 模拟累计 的表现对比，
  // 而不是只看当前这一个 running session 的盈亏。
  const modePnlSummary = useMemo(() => {
    const sum = (list: SessionListItem[]) => {
      const pnl = list.reduce((a, x) => a + (x.total_pnl || 0), 0);
      const trades = list.reduce((a, x) => a + (x.total_trades || 0), 0);
      // 后端返回的 win_rate 是百分比（0~100），需换算回绝对赢单数做加权
      const wins = list.reduce(
        (a, x) => a + Math.round(((x.win_rate || 0) / 100) * (x.total_trades || 0)),
        0,
      );
      return {
        pnl,
        trades,
        wins,
        winRate: trades > 0 ? (wins / trades) * 100 : 0,
        count: list.length,
      };
    };
    const live = sum(sessions.filter(s => s.trading_mode === 'live'));
    const paper = sum(sessions.filter(s => s.trading_mode !== 'live'));
    return { live, paper };
  }, [sessions]);

  // ── 会话选择器（始终显示） ──
  const renderSessionBar = () => (
    <div className="flex items-center gap-2 px-4 py-2 bg-gray-50 dark:bg-gray-900 border-b dark:border-gray-800 overflow-x-auto">
      <Bot className="w-4 h-4 text-violet-500 flex-shrink-0" />
      <span className="text-xs font-semibold text-muted-foreground flex-shrink-0">会话:</span>
      {sessions.map(s => {
        const isActive = s.session_id === activeSessionId;
        const statusIcon = s.status === 'running' ? <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" /> :
                           s.status === 'defensive' ? <span className="w-2 h-2 rounded-full bg-amber-500" /> :
                           s.status === 'paused' ? <span className="w-2 h-2 rounded-full bg-blue-500" /> :
                           <span className="w-2 h-2 rounded-full bg-gray-400" />;
        return (
          <button
            key={s.session_id}
            onClick={() => {
              setActiveSessionId(s.session_id);
              loadSessionDetail(s.session_id);
              setShowStartPanel(false);
            }}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all flex-shrink-0 ${
              isActive
                ? 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300 ring-1 ring-violet-400'
                : 'bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-400'
            }`}
          >
            {statusIcon}
            <span className="max-w-[140px] truncate font-medium" title={
              s.trading_mode === 'paper' && s.paper_account_name
                ? `交易员: ${s.account_name} | 资金池: ${s.paper_account_name}(#${s.paper_account_id})`
                : (s.account_name || `账户 #${s.account_id}`)
            }>
              {s.trading_mode === 'paper' && s.paper_account_name
                ? s.paper_account_name
                : (s.account_name || `账户 #${s.account_id}`)}
            </span>
            <span className="text-[10px] text-muted-foreground">({s.symbols?.length || 0}对)</span>
            {s.status === 'running' && <span className="text-[10px] text-green-600 dark:text-green-400">{s.total_trades || 0}笔</span>}
            <span
              onClick={e => { e.stopPropagation(); handleDelete(s.session_id); }}
              className="ml-1 p-0.5 rounded-full hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-600 flex-shrink-0"
              title="删除会话"
            >
              <X className="w-3 h-3" />
            </span>
          </button>
        );
      })}
      <button
        onClick={() => { setShowStartPanel(!showStartPanel); }}
        className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all flex-shrink-0 ${
          showStartPanel
            ? 'bg-violet-600 text-white'
            : 'bg-white dark:bg-gray-800 hover:bg-violet-100 dark:hover:bg-violet-900/30 text-violet-600 dark:text-violet-400 border border-dashed border-violet-300 dark:border-violet-700'
        }`}
      >
        <Plus className="w-3 h-3" />
        新建会话
      </button>
      <button
        onClick={() => { loadSessions(); }}
        className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 flex-shrink-0 ml-auto"
        title="刷新会话列表"
      >
        <RefreshCw className="w-3.5 h-3.5 text-muted-foreground" />
      </button>
    </div>
  );

  // ── 启动面板 ──
  const renderStartPanel = () => (
    <Card className="border-violet-200/50 dark:border-violet-800/30 bg-gradient-to-r from-violet-50/50 to-indigo-50/50 dark:from-violet-950/20 dark:to-indigo-950/20 mx-4 mt-3">
      <CardContent className="p-4">
        <div className="flex flex-col md:flex-row md:items-center gap-4">
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center">
              <Rocket className="w-5 h-5 text-white" />
            </div>
            <div>
              <h3 className="text-base font-bold">新会话</h3>
              <p className="text-xs text-muted-foreground">选择账户 + 交易对 → 启动</p>
            </div>
          </div>
          <div className="flex flex-col gap-3 flex-1 min-w-0">
            <div className="flex items-end gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground">交易员 (AI模型)</label>
                {accountsLoading ? (
                  <div className="h-9 min-w-[160px] border rounded-lg px-2.5 text-sm flex items-center text-muted-foreground">加载中...</div>
                ) : accountsError ? (
                  <button
                    onClick={() => loadAccounts()}
                    className="h-9 min-w-[160px] border border-red-300 rounded-lg px-2.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950 flex items-center gap-1"
                  >
                    <RefreshCw className="w-3 h-3" />加载失败，点击重试
                  </button>
                ) : (
                  <select
                    className="h-9 min-w-[160px] border rounded-lg px-2.5 text-sm bg-background"
                    value={selectedAccount ?? ''}
                    onChange={e => setSelectedAccount(Number(e.target.value) || null)}
                  >
                    <option value="" disabled>-- 选择交易员 --</option>
                    {traderAccounts.map(a => (
                      <option key={a.id} value={a.id}>{a.name || `账户 #${a.id}`}</option>
                    ))}
                  </select>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground">
                  交易所 <span className="text-muted-foreground/70">(留空=跟随账户配置)</span>
                </label>
                <select
                  className="h-9 min-w-[140px] border rounded-lg px-2.5 text-sm bg-background"
                  value={activeExchange ?? ''}
                  onChange={e => setActiveExchange(e.target.value || null)}
                >
                  <option value="">跟随账户配置</option>
                  <option value="hyperliquid">Hyperliquid</option>
                  <option value="binance">Binance</option>
                  <option value="asterdex">AsterDex</option>
                </select>
              </div>
              {tradingMode === 'paper' && (
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground">
                  模拟账户 (资金池) <span className="text-amber-600">*必选，真实下单账户</span>
                </label>
                {accountsLoading ? (
                  <div className="h-9 min-w-[160px] border rounded-lg px-2.5 text-sm flex items-center text-muted-foreground">加载中...</div>
                ) : accountsError ? (
                  <button
                    onClick={() => loadAccounts()}
                    className="h-9 min-w-[160px] border border-red-300 rounded-lg px-2.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950 flex items-center gap-1"
                  >
                    <RefreshCw className="w-3 h-3" />加载失败，点击重试
                  </button>
                ) : (
                  <select
                    className="h-9 min-w-[160px] border rounded-lg px-2.5 text-sm bg-background"
                    value={selectedPaperAccount ?? ''}
                    onChange={e => setSelectedPaperAccount(Number(e.target.value) || null)}
                  >
                    <option value="" disabled>-- 选择模拟账户 --</option>
                    {paperAccounts.map(a => (
                      <option key={a.id} value={a.id}>
                        {a.name || `账户 #${a.id}`} (#{a.id})
                      </option>
                    ))}
                  </select>
                )}
              </div>
              )}
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground">模式</label>
                <div className="flex gap-1">
                  <button
                    onClick={() => setTradingMode('paper')}
                    className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all flex items-center gap-1.5 ${
                      tradingMode === 'paper'
                        ? 'bg-amber-500 text-white shadow-lg shadow-amber-500/40 ring-2 ring-amber-600 scale-105'
                        : 'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-300 dark:hover:bg-gray-600 opacity-60 hover:opacity-100'
                    }`}
                  >
                    <Shield className="w-3.5 h-3.5" />{tradingMode === 'paper' && '✓ '}模拟盘
                  </button>
                  <button
                    onClick={() => setTradingMode('live')}
                    className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all flex items-center gap-1.5 ${
                      tradingMode === 'live'
                        ? 'bg-red-600 text-white shadow-lg shadow-red-600/40 ring-2 ring-red-800 scale-105'
                        : 'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-300 dark:hover:bg-gray-600 opacity-60 hover:opacity-100'
                    }`}
                  >
                    <Zap className="w-3.5 h-3.5" />{tradingMode === 'live' && '✓ '}实盘
                  </button>
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground flex items-center gap-1">
                  <Cpu className="w-3 h-3" />AI 自动选币
                </label>
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <div
                    onClick={() => setAutoCoinEnabled(!autoCoinEnabled)}
                    className={`relative w-10 h-5 rounded-full transition-colors ${
                      autoCoinEnabled ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600'
                    }`}
                  >
                    <div
                      className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                        autoCoinEnabled ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </div>
                  <span className={`text-xs font-medium ${autoCoinEnabled ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}`}>
                    {autoCoinEnabled ? '已开启 · AI 选币' : '已关闭 · 手动选币'}
                  </span>
                </label>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground flex items-center gap-1">
                  <ArrowLeftRight className="w-3 h-3" />套利模式
                </label>
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <div
                    onClick={() => setArbEnabled(!arbEnabled)}
                    className={`relative w-10 h-5 rounded-full transition-colors ${
                      arbEnabled ? 'bg-orange-500' : 'bg-gray-300 dark:bg-gray-600'
                    }`}
                  >
                    <div className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                      arbEnabled ? 'translate-x-5' : 'translate-x-0'
                    }`} />
                  </div>
                  <span className={`text-xs font-medium ${arbEnabled ? 'text-orange-600 dark:text-orange-400' : 'text-muted-foreground'}`}>
                    {arbEnabled ? '已开启 · 基差/返利套利' : '已关闭 · 仅方向性交易'}
                  </span>
                </label>
                {arbEnabled && (
                  <div className="text-[10px] text-muted-foreground leading-relaxed max-w-[220px]">
                    {profileLoading
                      ? '读取专用套利档案中...'
                      : arbitrageProfile?.id
                        ? `Profile #${arbitrageProfile.id} · ${(arbitrageProfile.enabled_strategies || []).join('/') || '未选策略'} · ${
                            arbitrageProfile.paper_account_mode === 'dedicated_arbitrage_paper'
                              ? `套利Paper#${arbitrageProfile.arbitrage_paper_account_id || '未选'}`
                              : '旧Paper资金池'
                          }`
                        : '未配置交易员级专用套利档案，将仅使用本次开关'}
                  </div>
                )}
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-medium text-muted-foreground flex items-center gap-1">
                  <Sparkles className="w-3 h-3" />风险偏好
                </label>
                <div className="flex gap-1">
                  {RISK_MODE_OPTIONS.map(opt => {
                    const active = riskMode === opt.value;
                    const Icon = opt.icon;
                    return (
                      <button
                        key={opt.value}
                        onClick={() => setRiskMode(opt.value)}
                        title={opt.desc}
                        className={`px-2.5 py-1.5 rounded-md text-xs font-bold transition-all flex items-center gap-1 ${
                          active
                            ? 'border-2 border-violet-600 bg-violet-100 dark:bg-violet-900/60 text-violet-800 dark:text-violet-200 shadow-md shadow-violet-300 dark:shadow-violet-900/50 scale-105'
                            : 'border-2 border-transparent bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-300 dark:hover:bg-gray-600 opacity-60 hover:opacity-100'
                        } ${opt.color}`}
                      >
                        <Icon className="w-3 h-3" />
                        {opt.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-medium text-muted-foreground">交易对</label>
              <div className="flex flex-wrap gap-1 items-center">
                {POPULAR_SYMBOLS.slice(0, 12).map(sym => {
                  const selected = selectedSymbols.includes(sym);
                  return (
                    <button
                      key={sym}
                      onClick={() => toggleSymbol(sym)}
                      className={`px-2 py-0.5 rounded-md text-xs font-semibold transition-all ${
                        selected
                          ? 'bg-violet-600 text-white dark:bg-violet-500'
                          : 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'
                      }`}
                    >
                      {selected && <Check className="w-3 h-3 inline mr-0.5" strokeWidth={3} />}
                      {sym}
                    </button>
                  );
                })}
                <div className="flex items-center gap-1 ml-1">
                  <Input
                    placeholder="自定义..."
                    value={customSymbolInput}
                    onChange={e => setCustomSymbolInput(e.target.value.toUpperCase())}
                    onKeyDown={e => e.key === 'Enter' && addCustomSymbol(customSymbolInput, setCustomSymbolInput, selectedSymbols, setSelectedSymbols)}
                    className="h-7 w-24 text-xs"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 px-2"
                    disabled={!customSymbolInput.trim()}
                    onClick={() => addCustomSymbol(customSymbolInput, setCustomSymbolInput, selectedSymbols, setSelectedSymbols)}
                  >
                    <Plus className="w-3 h-3" />
                  </Button>
                </div>
              </div>
            </div>
          </div>
          <div className="flex-shrink-0">
            <Button
              className="h-10 px-6 font-bold bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-700 hover:to-indigo-700"
              onClick={handleStart}
              disabled={
                starting
                || selectedSymbols.length === 0
                || selectedAccount == null
                || (tradingMode === 'paper' && !selectedPaperAccount)
              }
            >
              {starting ? (
                <><RefreshCw className="w-4 h-4 mr-1.5 animate-spin" />初始化中</>
              ) : (
                <><Rocket className="w-4 h-4 mr-1.5" />启动</>
              )}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );

  // ── 有活跃会话时显示仪表盘 ──
  if (activeSession) {
    const aiDecisionEvents = (activeSession.recent_events || []).filter(
      (evt) => !String(evt.event || '').startsWith('opencode_'),
    );
    const s = {
      ...activeSession,
      active_strategies: activeSession.active_strategies || [],
      symbols: activeSession.symbols || [],
      auto_coin_symbols: activeSession.auto_coin_symbols || [],
      recent_events: aiDecisionEvents,
    };
    const isRunning = s.status === 'running';
    const isDefensive = s.status === 'defensive';
    const isPaused = s.status === 'paused';
    const hasAutoCoin = s.auto_coin_enabled || s.auto_coin_symbols.length > 0;
    const displayAutoSymbols =
      (autoCoinStatus?.auto_symbols?.length ? autoCoinStatus.auto_symbols : s.auto_coin_symbols) || [];

    return (
      <div className="h-full flex flex-col overflow-auto w-full min-w-0">
        {renderSessionBar()}
        {showStartPanel && renderStartPanel()}
        {showStartPanel && <div className="h-0" />}
        <div className={`${showStartPanel ? 'hidden' : ''} p-4 gap-4 flex flex-col w-full min-w-0`}>
        {/* 顶部状态栏 — 三行布局：状态+按钮 / 标签 / 交易对 */}
        {/* 第一行：状态 + 操作按钮 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '3px 10px',
              fontSize: '13px', fontWeight: 600,
              color: isRunning ? '#15803d' : isDefensive ? '#c2410c' : isPaused ? '#a16207' : '#6b7280',
              backgroundColor: isRunning ? '#dcfce7' : isDefensive ? '#ffedd5' : isPaused ? '#fef9c3' : '#f3f4f6',
            }}>
              {isRunning && <span className="relative flex h-2 w-2"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" /><span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" /></span>}
              {isDefensive && <span className="relative flex h-2 w-2"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-orange-400 opacity-75" /><span className="relative inline-flex rounded-full h-2 w-2 bg-orange-500" /></span>}
              {isPaused && <Pause className="w-3.5 h-3.5" />}
              <Bot className="w-4 h-4" />
              {isRunning ? 'AI 自主交易运行中' : isDefensive ? 'AI 防守模式' : isPaused ? 'AI 交易已暂停' : '已停止'}
            </div>
            {/* 模拟盘/实盘 */}
            <span style={{
              padding: '2px 8px', fontSize: '11px', fontWeight: 700,
              color: s.trading_mode === 'live' ? '#b91c1c' : '#b45309',
              backgroundColor: s.trading_mode === 'live' ? '#fee2e2' : '#fef3c7',
              border: `1px solid ${s.trading_mode === 'live' ? '#fca5a5' : '#fcd34d'}`,
            }}>
              {s.trading_mode === 'live' ? '实盘' : '模拟盘'}
            </span>
            {/* 下单账户 */}
            {s.trading_mode === 'paper' && s.trading_account_id && (
              <span style={{ fontSize: '10px', color: '#1d4ed8', padding: '1px 6px', backgroundColor: '#eff6ff', border: '1px solid #bfdbfe' }}>
                #{s.trading_account_id}{sessions.find(sess => sess.session_id === s.session_id)?.paper_account_name ? ` · ${sessions.find(sess => sess.session_id === s.session_id)?.paper_account_name}` : ''}
              </span>
            )}
            {/* 资金池 */}
            <span style={{ fontSize: '10px', color: '#6b7280', padding: '1px 6px', backgroundColor: '#f9fafb' }}>
              {(() => {
                const li = sessions.find(sess => sess.session_id === s.session_id);
                if (s.trading_mode === 'paper' && (s.paper_account_id || li?.paper_account_name)) {
                  const nm = li?.paper_account_name || `模拟#${s.paper_account_id || s.trading_account_id}`;
                  return `资金池 ${nm}`;
                }
                return li?.account_name || `#${s.account_id}`;
              })()}
            </span>
            {/* AI 选币 */}
            {hasAutoCoin && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', padding: '1px 6px', fontSize: '11px', color: '#059669', backgroundColor: '#ecfdf5', border: '1px solid #a7f3d0' }}>
                <Cpu className="w-3 h-3" />
                AI选币{displayAutoSymbols.length > 0 ? `(${displayAutoSymbols.length})` : ''}
                {!s.auto_coin_enabled && displayAutoSymbols.length > 0 && (
                  <span style={{ fontSize: '9px', color: '#b45309' }}>已暂停</span>
                )}
              </span>
            )}
            {/* 风险等级 */}
            {(() => {
              const ra = s.current_risk_assessment;
              const eff = ra?.effective_level || s.risk_level;
              const isDynamic = s.risk_mode === 'ai_dynamic' || !s.risk_mode;
              const cfg: Record<string, { color: string; bg: string; label: string }> = {
                aggressive: { color: '#dc2626', bg: '#fef2f2', label: '激进' },
                conservative: { color: '#2563eb', bg: '#eff6ff', label: '保守' },
                moderate: { color: '#7c3aed', bg: '#f5f3ff', label: '均衡' },
              };
              const c = cfg[eff] || cfg.moderate;
              return (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '3px', padding: '1px 6px', fontSize: '11px', color: c.color, backgroundColor: c.bg }}
                  title={ra?.reason || ''}>
                  {isDynamic && <Brain className="w-3 h-3" />}
                  {isDynamic ? `AI·${c.label}` : c.label}
                  {ra?.market_volatility && (
                    <span style={{ fontSize: '9px', opacity: 0.7 }}>
                      ({ra.market_volatility === 'high' || ra.market_volatility === 'extreme' ? '高波动' :
                        ra.market_volatility === 'low' ? '低波动' : '正常'})
                    </span>
                  )}
                </span>
              );
            })()}
          </div>
          {/* 操作按钮 */}
          <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
            {isRunning && (
              <Button variant="outline" size="sm" onClick={() => handleAction('pause')}>
                <Pause className="w-4 h-4 mr-1" />暂停
              </Button>
            )}
            {(isPaused || isDefensive) && (
              <Button size="sm" onClick={() => handleAction('resume')}>
                <Play className="w-4 h-4 mr-1" />{isDefensive ? '强制恢复' : '恢复'}
              </Button>
            )}
            <Button variant="destructive" size="sm" onClick={() => {
              if (confirm('确定停止全自动交易？所有策略将被暂停。')) handleAction('stop');
            }}>
              <Square className="w-4 h-4 mr-1" />停止
            </Button>
            {s.auto_coin_enabled ? (
              <Button variant="outline" size="sm"
                onClick={() => handleScanNow(s.session_id)}
                disabled={autoCoinScanning}
                className="border-emerald-300 dark:border-emerald-700 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 text-xs"
              >
                <Search className={`w-4 h-4 mr-1 ${autoCoinScanning ? 'animate-spin' : ''}`} />
                {autoCoinScanning ? '扫描中' : '立即扫描'}
              </Button>
            ) : (
              <Button variant="outline" size="sm"
                onClick={() => handleToggleAutoCoin(s.session_id, true)}
                disabled={autoCoinScanning}
                className="border-emerald-300 dark:border-emerald-700 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20 text-xs"
              >
                <Cpu className="w-4 h-4 mr-1" />
                开启自动选币
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={() => loadSessionDetail(s.session_id)} disabled={refreshing}>
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>

        {/* 第二行：交易对列表 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap', padding: '2px 0' }}>
          {s.symbols.map(sym => (
            <span
              key={sym}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: '2px',
                padding: '1px 6px', fontSize: '11px', fontWeight: 500,
                backgroundColor: '#f3f4f6', color: '#374151',
                opacity: removingSymbol === sym ? 0.5 : 1,
              }}
            >
              {sym}/USDT
              {isRunning && s.symbols.length > 1 && (
                <span
                  onClick={() => handleRemoveSymbol(sym)}
                  style={{ marginLeft: '2px', cursor: removingSymbol ? 'default' : 'pointer', color: '#9ca3af', fontSize: '10px' }}
                  title={`移除 ${sym}`}
                >
                  ×
                </span>
              )}
            </span>
          ))}
          {(isRunning || isDefensive || isPaused) && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[11px] text-muted-foreground hover:text-foreground"
              onClick={() => setShowAddSymbols(prev => !prev)}
              disabled={addingSymbols}
            >
              <Plus className="w-3 h-3 mr-0.5" />
              添加
            </Button>
          )}
        </div>

        {/* 交易员心理状态（连亏冻结 / 谨慎模式） */}
        {s.trader_mental_state && (() => {
          const ms = s.trader_mental_state!;
          const isBlocking = ms.blocks_new_opens === true;
          const isCautious = ms.state === 'cautious';
          const showBanner = isBlocking || isCautious || (ms.consecutive_losses >= 2 && ms.state === 'normal');
          if (!showBanner) return null;
          const bypassPct = Math.round((ms.high_conf_bypass_threshold ?? 0.78) * 100);
          const reasonText = ms.block_reason || ms.hint || ms.description;
          if (isBlocking) {
            return (
              <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-700 animate-pulse">
                <Shield className="w-4 h-4 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
                <div className="text-sm min-w-0">
                  <div className="font-semibold text-red-700 dark:text-red-300">
                    连亏保护 · 新开仓已暂停
                    {ms.cooldown_remaining_min > 0 && (
                      <span className="font-normal ml-1">（约 {Math.ceil(ms.cooldown_remaining_min)} 分钟）</span>
                    )}
                  </div>
                  <p className="text-xs text-red-600/90 dark:text-red-300/90 mt-0.5 leading-relaxed">
                    {reasonText}
                  </p>
                  <p className="text-xs text-red-500/80 dark:text-red-400/80 mt-1">
                    AI 置信度 ≥ {bypassPct}% 时可试探小仓（仓位约 ×{(ms.size_multiplier > 0 ? ms.size_multiplier : 0.35).toFixed(2)}）
                  </p>
                </div>
              </div>
            );
          }
          if (isCautious) {
            return (
              <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700">
                <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                <div className="text-sm min-w-0">
                  <div className="font-semibold text-amber-800 dark:text-amber-300">
                    谨慎模式{ms.consecutive_losses > 0 ? ` · 连亏 ${ms.consecutive_losses} 笔` : ''}
                  </div>
                  <p className="text-xs text-amber-700/90 dark:text-amber-300/90 mt-0.5">
                    {reasonText} · 仓位系数 ×{ms.size_multiplier.toFixed(2)}
                  </p>
                </div>
              </div>
            );
          }
          return (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-orange-50/80 dark:bg-orange-900/10 border border-orange-100 dark:border-orange-800/50 text-xs">
              <AlertTriangle className="w-3.5 h-3.5 text-orange-500 flex-shrink-0" />
              <span className="text-orange-700 dark:text-orange-300">
                近期连亏 {ms.consecutive_losses} 笔 — {ms.description}
              </span>
            </div>
          );
        })()}

        {/* 系统健康告警横幅 */}
        {s.system_health && (!s.system_health.data_flow_ok || !s.system_health.ai_connection_ok || (s.system_health.active_alerts?.length ?? 0) > 0) && (
          <div className="space-y-1.5">
            {!s.system_health.data_flow_ok && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 animate-pulse">
                <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 flex-shrink-0" />
                <span className="text-sm font-semibold text-red-700 dark:text-red-300">
                  数据断流告警：部分交易对数据获取失败，分析结果可能不准确
                </span>
              </div>
            )}
            {!s.system_health.ai_connection_ok && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 animate-pulse">
                <Brain className="w-4 h-4 text-red-600 dark:text-red-400 flex-shrink-0" />
                <span className="text-sm font-semibold text-red-700 dark:text-red-300">
                  AI 连接告警：已连续{s.system_health.consecutive_ai_failures}次调用失败，当前使用规则引擎降级决策
                </span>
              </div>
            )}
            {(s.system_health.active_alerts || []).filter(a => a.severity === 'critical').slice(-3).map((alert, i) => (
              <div key={`crit-${i}`} className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-50/60 dark:bg-red-900/10 border border-red-100 dark:border-red-800/50 text-xs">
                <AlertTriangle className="w-3.5 h-3.5 text-red-500 flex-shrink-0 mt-0.5" />
                <div>
                  <span className="text-red-600 dark:text-red-400 font-medium">{alert.detail}</span>
                  <span className="text-muted-foreground ml-2">{fmtTime(alert.time)}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 添加交易对 */}
        {showAddSymbols && (isRunning || isDefensive || isPaused) && (
          <Card className="border-dashed">
            <CardContent className="py-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">选择要添加的交易对</span>
                <Button
                  size="sm"
                  disabled={symbolsToAdd.length === 0 || addingSymbols}
                  onClick={handleAddSymbols}
                >
                  {addingSymbols ? <RefreshCw className="w-3.5 h-3.5 animate-spin mr-1" /> : <Plus className="w-3.5 h-3.5 mr-1" />}
                  添加 {symbolsToAdd.length > 0 ? `(${symbolsToAdd.length})` : ''}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => { setShowAddSymbols(false); setSymbolsToAdd([]); }}>
                  取消
                </Button>
              </div>
              <div className="flex flex-wrap gap-1.5 items-center">
                {POPULAR_SYMBOLS.filter(sym => !s.symbols.includes(sym)).map(sym => {
                  const sel = symbolsToAdd.includes(sym);
                  return (
                    <button
                      key={sym}
                      onClick={() => toggleSymbolToAdd(sym)}
                      className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                        sel
                          ? 'bg-violet-600 text-white dark:bg-violet-500'
                          : 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'
                      }`}
                    >
                      {sym}
                    </button>
                  );
                })}
                <div className="flex items-center gap-1 ml-1">
                  <Input
                    placeholder="自定义..."
                    value={customAddInput}
                    onChange={e => setCustomAddInput(e.target.value.toUpperCase())}
                    onKeyDown={e => e.key === 'Enter' && addCustomSymbol(customAddInput, setCustomAddInput, [...s.symbols, ...symbolsToAdd], setSymbolsToAdd)}
                    className="h-7 w-24 text-xs"
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 px-2"
                    disabled={!customAddInput.trim()}
                    onClick={() => addCustomSymbol(customAddInput, setCustomAddInput, [...s.symbols, ...symbolsToAdd], setSymbolsToAdd)}
                  >
                    <Plus className="w-3 h-3" />
                  </Button>
                </div>
                {symbolsToAdd.filter(sym => !POPULAR_SYMBOLS.includes(sym)).map(sym => (
                  <span key={sym} className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md text-xs font-medium bg-violet-600 text-white dark:bg-violet-500">
                    {sym}
                    <button onClick={() => toggleSymbolToAdd(sym)} className="ml-0.5 hover:bg-violet-700 rounded-full p-0.5">
                      <X className="w-2.5 h-2.5" />
                    </button>
                  </span>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* 防守模式横幅 — v3 整改: 增加退出条件与剩余距离提示 */}
        {isDefensive && (() => {
          const _curDD = Math.max(0, s.current_drawdown || 0);
          const _maxDD = Math.max(0.01, s.config?.max_total_drawdown_pct || 0.2);
          // 退出阈值：回撤恢复到进入阈值的 80% 以下
          const _exitDD = _maxDD * 0.8;
          const _gap = _curDD - _exitDD;
          const _recoveredPct = _exitDD > 0 ? Math.min(1, (_exitDD - _curDD) / _exitDD + 1) : 0;
          return (
            <Card className="border-orange-300 dark:border-orange-700 bg-orange-50 dark:bg-orange-950/30">
              <CardContent className="py-3">
                <div className="flex items-start gap-3">
                  <Shield className="w-5 h-5 text-orange-600 mt-0.5 shrink-0" />
                  <div className="space-y-1 flex-1">
                    <p className="text-sm font-semibold text-orange-800 dark:text-orange-300">
                      防守模式 — AI 正在管理现有仓位
                    </p>
                    <p className="text-xs text-orange-600 dark:text-orange-400 leading-relaxed">
                      回撤触发风控 → 系统自动进入防守模式。AI 继续分析市场和现有仓位，可执行平仓/减仓操作，但不会开新仓。
                    </p>
                    <div className="mt-2 text-xs text-orange-700 dark:text-orange-300 space-y-0.5">
                      <div>
                        当前回撤：<span className="font-mono">{(_curDD * 100).toFixed(2)}%</span>
                        {'  '}·{'  '}进入阈值：<span className="font-mono">{(_maxDD * 100).toFixed(2)}%</span>
                        {'  '}·{'  '}退出阈值：<span className="font-mono">{(_exitDD * 100).toFixed(2)}%</span>
                      </div>
                      <div>
                        {_gap > 0 ? (
                          <>距离恢复还需降低 <span className="font-mono text-red-600 dark:text-red-400">{(_gap * 100).toFixed(2)}%</span> 回撤才会退出防守。</>
                        ) : (
                          <span className="text-green-700 dark:text-green-400">
                            回撤已低于退出阈值，等待下一轮循环切回运行模式…
                          </span>
                        )}
                      </div>
                      {_exitDD > 0 && (
                        <div className="w-full h-1.5 bg-orange-200 dark:bg-orange-900 rounded overflow-hidden">
                          <div
                            className="h-full bg-orange-500"
                            style={{ width: `${Math.max(0, Math.min(100, _recoveredPct * 100)).toFixed(1)}%` }}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })()}

        {/* 账户累计（按实盘 / 模拟分开汇总，来源：最近 20 个 session） */}
        {(modePnlSummary.live.count > 0 || modePnlSummary.paper.count > 0) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(['live', 'paper'] as const).map(mode => {
              const g = mode === 'live' ? modePnlSummary.live : modePnlSummary.paper;
              const isLive = mode === 'live';
              const pnlPositive = g.pnl >= 0;
              const hasData = g.count > 0;
              return (
                <div
                  key={mode}
                  className={`rounded-lg border px-3 py-2 flex items-center justify-between ${
                    isLive
                      ? 'bg-red-50/50 border-red-200 dark:bg-red-950/20 dark:border-red-800'
                      : 'bg-amber-50/50 border-amber-200 dark:bg-amber-950/20 dark:border-amber-800'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-bold ${isLive ? 'text-red-700 dark:text-red-400' : 'text-amber-700 dark:text-amber-400'}`}>
                      {isLive ? '⚡ 实盘累计' : '🛡 模拟累计'}
                    </span>
                    {hasData && (
                      <span className="text-[10px] text-muted-foreground">
                        {g.count} 个 session · {g.trades} 笔 · 胜率 {g.winRate.toFixed(1)}%
                      </span>
                    )}
                  </div>
                  <span
                    className={`text-base font-bold tabular-nums ${
                      !hasData
                        ? 'text-muted-foreground'
                        : pnlPositive
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-red-600 dark:text-red-400'
                    }`}
                  >
                    {hasData
                      ? `${pnlPositive ? '+' : '-'}$${Math.abs(g.pnl).toFixed(2)}`
                      : '—'}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* 核心指标（当前活跃 session 的实时快照） */}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
          <MetricCard icon={<TrendingUp />} label="本次运行盈亏" value={`${s.total_pnl >= 0 ? '+' : '-'}$${Math.abs(s.total_pnl).toFixed(2)}`} color={s.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'} />
          <MetricCard icon={<BarChart3 />} label="总交易" value={String(s.total_trades)} />
          <MetricCard icon={<Target />} label="胜率" value={`${s.win_rate.toFixed(1)}%`} color={s.win_rate >= 50 ? 'text-green-600' : 'text-orange-600'} />
          <MetricCard icon={<TrendingDown />} label="当前回撤" value={`${((s.current_drawdown || 0) * 100).toFixed(1)}%`} color={(s.current_drawdown || 0) > (s.config?.max_total_drawdown_pct || 0.2) ? 'text-red-600' : (s.current_drawdown || 0) > 0.10 ? 'text-orange-600' : 'text-muted-foreground'} />
          <MetricCard icon={<Zap />} label="活跃策略" value={`${s.active_strategies.length}/${s.total_strategies_created}`} />
          <MetricCard icon={<Clock />} label="运行时长" value={s.started_at ? formatDuration(s.started_at) : '--'} />
        </div>

        {/* 多路分析师报告 — 按 tier 分组展示 */}
        {(() => {
          const allReports = s.analyst_reports || {};
          if (Object.keys(allReports).length === 0) return null;
          // 后端返回的 key 格式: "{tier}_{role}" 如 long_position, short_kline
          const tiers = ['short', 'mid', 'long'] as const;
          const roles = ['position', 'market', 'intel', 'risk', 'strategy', 'kline'] as const;
          const roleIcons: Record<string, React.ReactNode> = {
            position: <Activity className="w-3.5 h-3.5" />,
            market: <TrendingUp className="w-3.5 h-3.5" />,
            intel: <Eye className="w-3.5 h-3.5" />,
            risk: <Shield className="w-3.5 h-3.5" />,
            strategy: <Target className="w-3.5 h-3.5" />,
            kline: <BarChart3 className="w-3.5 h-3.5" />,
          };
          const tierColors: Record<string, string> = {
            short: 'border-blue-300 dark:border-blue-700',
            mid: 'border-purple-300 dark:border-purple-700',
            long: 'border-amber-300 dark:border-amber-700',
          };
          const tierLabels: Record<string, string> = {
            short: '短线(日内)',
            mid: '波段(1–3天)',
            long: '长线',
          };
          // 判断有哪些 tier 有数据
          const activeTiers = tiers.filter(t => roles.some(r => allReports[`${t}_${r}`]));
          if (activeTiers.length === 0) return null;
          return (
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Brain className="w-4 h-4" />
                  多路分析师报告
                  <span className="text-[10px] text-muted-foreground font-normal">
                    ({activeTiers.length} 层级 · {Object.keys(allReports).length} 位分析师)
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="py-0 pb-3 space-y-3">
                {activeTiers.map(tier => {
                  const tierReports = roles
                    .map(role => ({ role, report: (allReports as any)[`${tier}_${role}`] as any }))
                    .filter(({ report }) => report);
                  if (tierReports.length === 0) return null;
                  return (
                    <div key={tier}>
                      <div className="flex items-center gap-1.5 mb-1.5">
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${tier === 'short' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' : tier === 'mid' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'}`}>
                          {tierLabels[tier]}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
                        {tierReports.map(({ role, report }) => {
                          if (!report) return null;
                          const riskPct = report.risk_score || 50;
                          const riskColor = riskPct > 75 ? 'text-red-600 bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-800'
                            : riskPct > 60 ? 'text-orange-600 bg-orange-50 dark:bg-orange-950/30 border-orange-200 dark:border-orange-800'
                            : riskPct > 35 ? 'text-gray-600 bg-gray-50 dark:bg-gray-900/30 border-gray-200 dark:border-gray-700'
                            : 'text-green-600 bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800';
                          return (
                            <div key={`${tier}_${role}`} className={`rounded-lg border p-2 ${riskColor}`}>
                              <div className="flex items-center gap-1 mb-0.5">
                                {roleIcons[role]}
                                <span className="text-[10px] font-semibold truncate">{report.analyst}</span>
                                <span className="ml-auto text-[9px] font-mono opacity-70">{riskPct.toFixed(0)}</span>
                              </div>
                              <p className="text-[10px] leading-tight opacity-90 line-clamp-2">{report.summary}</p>
                              {report.signals?.slice(0, 2).map((sig, i) => (
                                <div key={i} className="flex items-center gap-0.5 mt-0.5">
                                  <span className="text-[8px]">
                                    {sig.signal === 'danger' ? '🔴' : sig.signal === 'warning' ? '🟡' : sig.signal === 'bullish' ? '🟢' : '⚪'}
                                  </span>
                                  <span className="text-[9px] opacity-80 truncate">{sig.detail}</span>
                                </div>
                              ))}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          );
        })()}

        {/* AI 自动选币状态 */}
        {(isRunning || isDefensive || isPaused) && (
          <Card className="border-emerald-200 dark:border-emerald-800 bg-gradient-to-r from-emerald-50/40 to-transparent dark:from-emerald-950/20">
            <CardHeader className="py-3 pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2 flex-wrap">
                <RefreshCw className={`w-4 h-4 ${autoCoinScanning ? 'text-emerald-500 animate-spin' : autoCoinStatus?.running ? 'text-emerald-500' : 'text-muted-foreground'}`} />
                AI 自动选币
                {s.auto_coin_enabled && autoCoinStatus?.running && (
                  <span className="ml-1 px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300 text-[10px] font-medium">
                    运行中
                  </span>
                )}
                {!s.auto_coin_enabled && (
                  <span className="ml-1 px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 text-[10px] font-medium">
                    未开启
                  </span>
                )}
                {s.auto_coin_enabled && !autoCoinStatus?.running && displayAutoSymbols.length > 0 && (
                  <span className="ml-1 px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 text-[10px] font-medium">
                    扫描已暂停
                  </span>
                )}
                {autoCoinCountdown != null && s.auto_coin_enabled && (
                  <span className="ml-auto text-[10px] text-muted-foreground tabular-nums">
                    下次扫描: {Math.floor(autoCoinCountdown / 60)}分{autoCoinCountdown % 60}秒
                  </span>
                )}
                <div className="ml-auto flex items-center gap-2">
                  {s.auto_coin_enabled ? (
                    <>
                      <Button variant="outline" size="sm" className="h-7 text-xs"
                        onClick={() => handleScanNow(s.session_id)}
                        disabled={autoCoinScanning}
                      >
                        {autoCoinScanning ? '扫描中...' : '立即扫描'}
                      </Button>
                      <Button variant="ghost" size="sm" className="h-7 text-xs text-muted-foreground"
                        onClick={() => handleToggleAutoCoin(s.session_id, false)}
                        disabled={autoCoinScanning}
                      >
                        关闭
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700"
                      onClick={() => handleToggleAutoCoin(s.session_id, true)}
                      disabled={autoCoinScanning}
                    >
                      开启自动选币
                    </Button>
                  )}
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="py-0 pb-3">
              {!s.auto_coin_enabled ? (
                <div className="text-xs text-muted-foreground py-2 space-y-1">
                  <p>
                    当前会话未开启「本会话自动跟投短线」，只会交易你手动选择的币种（{s.symbols.join(' / ')}）。
                  </p>
                  <p className="text-amber-400/90">
                    推荐使用 VIP 共用 AI 选币页查看短线/长线理由并手动加入会话（主产品路径）；
                    此处开关仅为兼容：开启后仍按旧逻辑对本会话自动注入短线池。
                  </p>
                </div>
              ) : !autoCoinStatusLoaded && displayAutoSymbols.length === 0 ? (
                <div className="text-xs text-muted-foreground py-2 flex items-center gap-2">
                  <RefreshCw className="w-3 h-3 animate-spin" /> 获取选币状态...
                </div>
              ) : (
                <div className="space-y-3">
                  {/* 当前选中的自动币种 */}
                  <div>
                    <div className="text-[10px] font-medium text-muted-foreground mb-1.5">当前自动选币</div>
                    {displayAutoSymbols.length === 0 ? (
                      <span className="text-xs text-muted-foreground">
                        {!autoCoinStatus && autoCoinStatusLoaded
                          ? '选币服务暂不可用（请确认后端已启动后刷新页面）'
                          : autoCoinStatus?.inject_blocked_reason
                          ? autoCoinStatus.inject_blocked_reason
                          : autoCoinStatus?.last_scan_at
                            ? '扫描已完成，本轮未注入新币种（可能已在列表中或评分未达替换条件）'
                            : '暂无自动选币（开启后约 1 小时内首次扫描，或点「立即扫描」）'}
                      </span>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {displayAutoSymbols.map(sym => {
                          const info = autoCoinStatus?.candidate_pool?.active?.[sym];
                          return (
                            <span key={sym} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-100 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 text-xs font-mono text-emerald-800 dark:text-emerald-200">
                              {sym}
                              {info != null && (
                                <span className="text-[9px] text-emerald-600 dark:text-emerald-400 ml-0.5">
                                  {info.ai_confidence != null ? `${Math.round(info.ai_confidence * 100)}%` : `${Math.round(info.score * 100)}分`}
                                </span>
                              )}
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  {/* 候选池统计 */}
                  {autoCoinStatus?.candidate_pool && (
                    <div className="flex gap-3 text-[10px]">
                      <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
                        <span className="w-2 h-2 rounded-full bg-emerald-400" />
                        活跃 {Object.keys(autoCoinStatus.candidate_pool.active || {}).length}/{autoCoinStatus.candidate_pool.max_active}
                      </span>
                      <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                        <span className="w-2 h-2 rounded-full bg-amber-400" />
                        冷却 {autoCoinStatus.candidate_pool.cooling_count}
                      </span>
                      <span className="flex items-center gap-1 text-gray-500">
                        <span className="w-2 h-2 rounded-full bg-gray-400" />
                        黑名单 {autoCoinStatus.candidate_pool.blacklist_count}
                      </span>
                    </div>
                  )}

                  {/* 上次扫描时间 */}
                  {autoCoinStatus?.last_scan_at && (
                    <div className="text-[10px] text-muted-foreground">
                      上次扫描: {new Date(autoCoinStatus.last_scan_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </div>
                  )}
                  {autoCoinStatus?.error && (
                    <div className="text-[10px] text-amber-700 dark:text-amber-300">
                      {autoCoinStatus.error}
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* 活跃策略列表 */}
        <Card>
          <CardHeader className="py-3" style={{ cursor: 'pointer', userSelect: 'none' }} onClick={() => setStrategyListCollapsed(c => !c)}>
            <CardTitle className="text-sm flex flex-wrap items-center gap-2">
              {strategyListCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              AI 管理的策略 ({s.active_strategies.length} 个运行中 / {s.terminated_count} 个已淘汰)
              <span className={`ml-auto px-2 py-0.5 rounded text-[10px] font-bold ${
                s.trading_mode === 'live'
                  ? 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400'
                  : 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400'
              }`}>
                {s.trading_mode === 'live' ? '实盘交易' : '模拟交易'}
              </span>
              {s.status !== 'stopped' && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 text-[11px] shrink-0"
                  disabled={mergingDup}
                  onClick={(e) => { e.stopPropagation(); mergeDuplicateStrategies(); }}
                >
                  {mergingDup ? '合并中…' : '合并重复策略'}
                </Button>
              )}
            </CardTitle>
          </CardHeader>
          {!strategyListCollapsed && (
          <CardContent className="pt-0">
            {s.active_strategies.length === 0 ? (
              <div className="text-center py-6 text-sm text-muted-foreground">
                <Bot className="w-8 h-8 mx-auto mb-2 opacity-30" />
                AI 正在分析市场环境，即将自动创建策略…
              </div>
            ) : (() => {
              const grouped = strategyGrouped;
              /** 后端 legacy 槽位字段：仅作区分实例，不再展示「短/中/长线」话术 */
              const legacyTierFromStrategy = (st: (typeof s.active_strategies)[0]) => {
                const t = (st.timeframe_tier || '').toLowerCase();
                if (t === 'short' || t === 'mid' || t === 'long') return t;
                const n = st.name || '';
                if (n.includes('短线')) return 'short';
                if (n.includes('中线')) return 'mid';
                if (n.includes('长线')) return 'long';
                return 'unified';
              };
              const instanceBadge: Record<string, { label: string; color: string; icon: string }> = {
                short: { label: '实例·快', color: 'bg-violet-100 text-violet-800 dark:bg-violet-900/35 dark:text-violet-300', icon: '◇' },
                mid: { label: '实例·衡', color: 'bg-slate-100 text-slate-800 dark:bg-slate-800/60 dark:text-slate-300', icon: '◇' },
                long: { label: '实例·缓', color: 'bg-cyan-100 text-cyan-900 dark:bg-cyan-900/35 dark:text-cyan-300', icon: '◇' },
                unified: { label: '自主策略', color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/35 dark:text-emerald-300', icon: '🤖' },
              };
              return (
                <div className="space-y-3">
                  {Object.entries(grouped).map(([sym, strats]) => (
                    <div key={sym} className="border rounded-lg p-2 dark:border-gray-700">
                      <div className="flex items-center gap-2 mb-2 px-1 flex-wrap">
                        <span className="font-bold text-sm">{sym}/USDT</span>
                        <span className="text-[10px] text-muted-foreground">
                          {strats.length > 1
                            ? `${strats.length} 个策略实例（短线/中线/长线多周期覆盖）`
                            : '1 个策略'}
                        </span>
                      </div>
                      <div className="grid grid-cols-1 gap-1">
                        {strats.map(strat => {
                          const tierKey = legacyTierFromStrategy(strat);
                          const si = instanceBadge[tierKey] || instanceBadge.unified;
                          return (
                            <div
                              key={strat.strategy_id}
                              role="button"
                              tabIndex={0}
                              onClick={() => onNavigateToStrategy?.(strat.strategy_id)}
                              onKeyDown={(e) => e.key === 'Enter' && onNavigateToStrategy?.(strat.strategy_id)}
                              className={`flex items-center justify-between p-2 rounded bg-gray-50 dark:bg-gray-800/40 text-xs ${onNavigateToStrategy ? 'cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800/70 transition-colors' : ''}`}
                            >
                              <div className="flex items-center gap-2 flex-1 min-w-0">
                                <span>{si.icon}</span>
                                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${si.color}`}>{si.label}</span>
                                <span className="truncate text-muted-foreground">{strat.name.replace(/AI自主_/, '').replace(new RegExp(`_${sym}_\\d+`), '')}</span>
                                {strat.status === 'active' ? (
                                  <span className="relative flex h-1.5 w-1.5 flex-shrink-0"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" /><span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-green-500" /></span>
                                ) : strat.status === 'paused' ? (
                                  <span className="px-1 py-0.5 rounded text-[9px] bg-yellow-100 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400">暂停</span>
                                ) : null}
                              </div>
                              <div className="flex items-center gap-3 flex-shrink-0">
                                <span>{strat.total_trades}笔</span>
                                <span className={strat.win_rate >= 50 ? 'text-green-600' : strat.win_rate > 0 ? 'text-orange-600' : 'text-muted-foreground'}>
                                  {strat.win_rate.toFixed(0)}%
                                </span>
                                <span className={`font-semibold ${strat.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {strat.total_pnl >= 0 ? '+' : '-'}${Math.abs(strat.total_pnl).toFixed(2)}
                                </span>
                                {onNavigateToStrategy && (
                                  <ChevronRight className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" title="查看详情" />
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })()}
          </CardContent>
          )}
        </Card>

        {/* 市场概览 + 事件日志 — 两栏等宽，min-w-0 防止日志被裁切 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 w-full min-w-0 items-start">
          {/* 市场概览 */}
          <Card className="min-w-0 w-full">
            <CardHeader className="py-3 px-4">
              <CardTitle className="text-sm flex items-center gap-2">
                <CircleDot className="w-4 h-4" /> 市场概览
                {s.last_health_check && <span className="text-[10px] text-muted-foreground font-normal ml-auto">{parseUTC(s.last_health_check)?.toLocaleTimeString('zh-CN') || ''}</span>}
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0 px-4 space-y-2 min-w-0">
              {s.last_market_summary ? Object.entries(s.last_market_summary).map(([sym, info]: [string, any]) => (
                <div key={sym} className="text-xs p-2 rounded bg-gray-50 dark:bg-gray-800/50 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{sym}/USDT</span>
                    {info.error ? (
                      <span className="text-red-500">{info.error}</span>
                    ) : (
                      <div className="flex items-center gap-3">
                        <span>${(() => {
                          const px = info.current_price
                            || (Array.isArray(info.kline_data) && info.kline_data.length
                              ? info.kline_data[info.kline_data.length - 1]?.close
                              : null);
                          return px ? Number(px).toLocaleString(undefined, { maximumFractionDigits: px < 1 ? 4 : 2 }) : '--';
                        })()}</span>
                        <span className={info.trend_direction === 'bullish' ? 'text-green-600' : info.trend_direction === 'bearish' ? 'text-red-600' : 'text-gray-500'}>
                          {info.trend_direction === 'bullish' ? '↗看多' : info.trend_direction === 'bearish' ? '↘看空' : info.trend_direction === 'neutral' ? '→中性' : '→--'}
                        </span>
                        <span className="text-muted-foreground">
                          {info.market_cycle === 'bull' ? '牛市' : info.market_cycle === 'bear' ? '熊市' : info.market_cycle === 'sideways' ? '震荡' : '--'}
                        </span>
                      </div>
                    )}
                  </div>
                  {!info.error && (info.sentiment_index !== undefined || info.whale_direction !== undefined) && (
                    <div className="flex items-center gap-2 text-[10px] pl-1">
                      {info.sentiment_index !== undefined && (
                        <span className={
                          info.sentiment_index < 25 ? 'text-red-500' :
                          info.sentiment_index > 75 ? 'text-green-500' : 'text-amber-500'
                        }>
                          {info.sentiment_index < 25 ? '😰' : info.sentiment_index > 75 ? '🤑' : '😐'}
                          情绪{Math.round(info.sentiment_index)}
                        </span>
                      )}
                      {info.whale_direction !== undefined && Math.abs(info.whale_direction) > 0.05 && (
                        <span className={info.whale_direction > 0 ? 'text-green-600' : 'text-red-600'}>
                          🐋{info.whale_direction > 0 ? '买' : '卖'}{Math.abs(info.whale_direction).toFixed(2)}
                        </span>
                      )}
                      {info.derivatives_signal && info.derivatives_signal !== 'neutral' && (
                        <span className={info.derivatives_signal === 'bullish' ? 'text-green-600' : 'text-red-600'}>
                          📊{info.derivatives_signal === 'bullish' ? '多' : '空'}
                        </span>
                      )}
                      {info.funding_rate !== undefined && info.funding_rate !== 0 && (
                        <span className="text-muted-foreground">
                          费率{(info.funding_rate * 100).toFixed(3)}%
                        </span>
                      )}
                    </div>
                  )}
                  {/* 编排器智能槽位推荐 */}
                  {info.orchestrator && (
                    <div className="mt-1.5 pt-1.5 border-t dark:border-gray-700/50 space-y-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-muted-foreground">编排器</span>
                        {(() => {
                          const orch = info.orchestrator;
                          const biasColor = (b: string) => b === 'bullish' ? 'text-green-600' : b === 'bearish' ? 'text-red-600' : 'text-gray-400';
                          const confOf = (tier: 'long' | 'mid' | 'short') =>
                            Number(orch[`${tier}_conf`] ?? orch[`${tier}_confidence`] ?? 0);
                          const biasText = (b: string, c: number) => `${b === 'bullish' ? '多' : b === 'bearish' ? '空' : '中性'}${(c * 100).toFixed(0)}%`;
                          return (
                            <div className="flex items-center gap-2 text-[10px]">
                              <span className={biasColor(orch.long_bias)}>长{biasText(orch.long_bias, confOf('long'))}</span>
                              <span className={biasColor(orch.mid_bias)}>中{biasText(orch.mid_bias, confOf('mid'))}</span>
                              <span className={biasColor(orch.short_bias)}>短{biasText(orch.short_bias, confOf('short'))}</span>
                            </div>
                          );
                        })()}
                      </div>
                      {Array.isArray(info.orchestrator.recommended_slots) && (
                        <div className="flex items-center gap-1 flex-wrap">
                          {(() => {
                            const slots: string[] = info.orchestrator.recommended_slots || [];
                            if (slots.includes('active')) {
                              return (
                                <span
                                  title={(info.orchestrator.slot_reasoning && (info.orchestrator.slot_reasoning as any).active) || info.orchestrator.reasoning || ''}
                                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 ring-1 ring-green-300/50"
                                >
                                  ✓ 允许新开仓
                                </span>
                              );
                            }
                            if (slots.length === 0) {
                              return (
                                <span
                                  title={info.orchestrator.reasoning || ''}
                                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                                >
                                  ○ 暂不推荐新开仓
                                </span>
                              );
                            }
                            return ['short', 'mid', 'long'].map((slot) => {
                              const labels: Record<string, string> = { short: '快', mid: '衡', long: '缓' };
                              const icons: Record<string, string> = { short: '⚡', mid: '📊', long: '🎯' };
                              const isActive = slots.includes(slot);
                              const action = (info.orchestrator.slot_actions || {})[slot] || '';
                              const reason = (info.orchestrator.slot_reasoning || {})[slot] || '';
                              return (
                                <span
                                  key={slot}
                                  title={reason}
                                  className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] cursor-default transition-all ${
                                    isActive
                                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 ring-1 ring-green-300/50'
                                      : action === 'pause'
                                      ? 'bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500 line-through'
                                      : 'bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-500'
                                  }`}
                                >
                                  {icons[slot]} {labels[slot]}
                                  {isActive && <span className="ml-0.5 text-green-500 font-bold">ON</span>}
                                  {!isActive && action === 'pause' && <span className="ml-0.5">OFF</span>}
                                </span>
                              );
                            });
                          })()}
                        </div>
                      )}
                      {info.orchestrator.reasoning && (
                        <div className="text-[10px] text-muted-foreground italic truncate" title={info.orchestrator.reasoning}>
                          {info.orchestrator.reasoning}
                        </div>
                      )}
                    </div>
                  )}
                  {/* 短线因子 ScalpFactorRouter 实时评分 */}
                  {info.scalp_factor && (
                    <div className="mt-1.5 pt-1.5 border-t dark:border-gray-700/50 space-y-0.5">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px] text-muted-foreground">⚡短线因子</span>
                        {(() => {
                          const sf = info.scalp_factor;
                          const score = Number(sf.factor_score ?? 0);
                          const thresh = Number(sf.threshold ?? 25);
                          const dir = String(sf.direction || 'neutral');
                          const act = String(sf.action || 'hold');
                          const scoreColor = score >= thresh ? 'text-green-600' : score >= thresh * 0.7 ? 'text-amber-600' : 'text-gray-500';
                          const dirLabel = dir === 'long' ? '多' : dir === 'short' ? '空' : '中性';
                          const actLabel = act === 'buy' ? '买入' : act === 'sell' ? '卖出' : '观望';
                          return (
                            <>
                              <span className={`text-[10px] font-semibold ${scoreColor}`}>
                                {score}分 / 门槛{thresh}
                              </span>
                              <span className="text-[10px] text-muted-foreground">{dirLabel}·{actLabel}</span>
                            </>
                          );
                        })()}
                      </div>
                      {info.scalp_factor.reasoning && (
                        <div className="text-[10px] text-muted-foreground italic truncate" title={info.scalp_factor.reasoning}>
                          {info.scalp_factor.reasoning}
                        </div>
                      )}
                      {info.scalp_factor.breakdown && typeof info.scalp_factor.breakdown === 'object' && (
                        <div className="text-[10px] text-muted-foreground truncate" title={JSON.stringify(info.scalp_factor.breakdown)}>
                          分解: {Object.entries(info.scalp_factor.breakdown).slice(0, 3).map(([k, v]) => `${k}=${v}`).join(' · ')}
                        </div>
                      )}
                    </div>
                  )}
                  {/* 短线参谋 ScalpExecutionLane advisory */}
                  {info.scalp_advisory && (
                    <div className="mt-1.5 pt-1.5 border-t dark:border-gray-700/50 space-y-0.5">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px] text-muted-foreground">⚡短线参谋</span>
                        {(() => {
                          const adv = info.scalp_advisory;
                          const verdictColor: Record<string, string> = {
                            allow_long: 'text-green-600',
                            allow_short: 'text-red-600',
                            avoid: 'text-amber-600',
                            neutral: 'text-gray-400',
                          };
                          const verdictLabel: Record<string, string> = {
                            allow_long: '允许多',
                            allow_short: '允许空',
                            avoid: '回避',
                            neutral: '中性',
                          };
                          const v = adv.advisory_verdict || 'neutral';
                          return (
                            <span className={`text-[10px] font-medium ${verdictColor[v] || verdictColor.neutral}`}>
                              {verdictLabel[v] || v}
                              {adv.penalty ? ` +${adv.penalty}分门槛` : ''}
                            </span>
                          );
                        })()}
                        {info.scalp_advisory.regime && (
                          <span className="text-[10px] text-muted-foreground">
                            状态:{info.scalp_advisory.regime}
                          </span>
                        )}
                        {info.scalp_advisory.range_position_5m != null && (
                          <span className="text-[10px] text-muted-foreground">
                            区间{(Number(info.scalp_advisory.range_position_5m) * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>
                      {Array.isArray(info.scalp_advisory.stop_clusters) && info.scalp_advisory.stop_clusters.length > 0 && (
                        <div className="text-[10px] text-amber-600 truncate" title={info.scalp_advisory.stop_clusters.join('; ')}>
                          止损区: {info.scalp_advisory.stop_clusters.slice(0, 2).join(' · ')}
                        </div>
                      )}
                      {info.scalp_advisory.notes && (
                        <div className="text-[10px] text-muted-foreground italic truncate" title={info.scalp_advisory.notes}>
                          {info.scalp_advisory.notes}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )) : <div className="text-center py-4 text-xs text-muted-foreground">等待首次市场扫描...</div>}
            </CardContent>
          </Card>

          {/* 右栏：MLTO（可折叠）+ 决策日志 */}
          <div className="flex flex-col gap-3 min-w-0 min-h-0">
            {s.session_id && (
              <MidLongThesisPanel
                sessionId={s.session_id}
                refreshSec={30}
                defaultCollapsed
                watchSymbols={[...new Set([...(s.symbols || []), ...Object.keys(s.last_market_summary || {})])]}
              />
            )}

            <Card className="min-w-0 w-full flex-1 flex flex-col min-h-[320px]">
            <CardHeader className="py-3 px-4 shrink-0">
              <CardTitle className="text-sm flex items-center gap-2">
                <Activity className="w-4 h-4" /> AI 决策日志
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0 px-4 min-w-0 flex-1 overflow-hidden">
              <div className="h-full max-h-[min(55vh,520px)] overflow-y-auto overflow-x-hidden w-full min-w-0 space-y-0.5">
                {(() => {
                  // 中线/长线决策优先显示，不被大量短线决策挤出
                  const allEvents = (s.recent_events || []).slice().reverse();
                  const isMidLong = (detail: string) =>
                    detail.includes('[中线]') || detail.includes('[长线]') || detail.includes('[MLTO]');
                  const midLong = allEvents.filter(e => e.event === 'master_decision' && isMidLong(e.detail || ''));
                  const others = allEvents.filter(e => !(e.event === 'master_decision' && isMidLong(e.detail || '')));
                  // 中线/长线全部显示 + 其他保留最近条目，合并后按时间排序
                  const merged = [...midLong, ...others.slice(0, 60)];
                  merged.sort((a, b) => (b.time || '').localeCompare(a.time || ''));
                  return merged.slice(0, 80);
                })().map((evt, i) => (
                  <div key={i} className={`flex gap-2 text-xs py-1.5 border-b dark:border-gray-800 last:border-0 min-w-0 w-full ${
                    evt.severity === 'critical' ? 'bg-red-50/50 dark:bg-red-900/10 text-red-700 dark:text-red-300 font-medium' :
                    evt.severity === 'warning' ? 'bg-amber-50/50 dark:bg-amber-900/10 text-amber-700 dark:text-amber-300' :
                    (evt.detail || '').includes('[MLTO]') ? 'bg-purple-50/40 dark:bg-purple-900/15' :
                    (evt.detail || '').includes('[中线]') ? 'bg-blue-50/30 dark:bg-blue-900/10' :
                    (evt.detail || '').includes('[长线]') ? 'bg-purple-50/30 dark:bg-purple-900/10' :
                    (evt.detail || '').includes('[短线') || evt.event === 'scalp_scan' ? 'bg-orange-50/30 dark:bg-orange-900/10' : ''
                  }`}>
                    <span className="text-muted-foreground flex-shrink-0 w-10 tabular-nums">{fmtTime(evt.time)}</span>
                    <span className="flex-shrink-0 pt-0.5"><EventIcon event={evt.event} /></span>
                    <span className={`flex-1 min-w-0 whitespace-pre-wrap break-words [overflow-wrap:anywhere] leading-relaxed ${evt.severity === 'critical' ? 'font-semibold' : ''}`}>{evt.detail}</span>
                  </div>
                ))}
                {(!s.recent_events || s.recent_events.length === 0) && (
                  <div className="text-center py-4 text-xs text-muted-foreground">暂无事件</div>
                )}
              </div>
            </CardContent>
          </Card>
          </div>
        </div>
      </div>
      </div>
    );
  }

  // ── 无活跃会话详情时：显示会话栏 + 可选启动面板 ──
  return (
    <div className="p-0">
      {renderSessionBar()}
      {showStartPanel && renderStartPanel()}
      {!showStartPanel && !activeSession && (
        <div className="p-8 text-center">
          <Bot className="w-12 h-12 mx-auto mb-3 text-violet-300" />
          <p className="text-sm text-muted-foreground">选择一个会话查看详情，或点击「新建会话」启动新的全自动交易</p>
        </div>
      )}
    </div>
  );
}

const MetricCard = memo(function MetricCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: string; color?: string }) {
  return (
    <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-800/50">
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground mb-1">
        <span className="w-3.5 h-3.5 opacity-50">{icon}</span>{label}
      </div>
      <div className={`text-lg font-bold ${color || ''}`}>{value}</div>
    </div>
  );
});

const EventIcon = memo(function EventIcon({ event }: { event: string }) {
  switch (event) {
    case 'strategy_created': return <CheckCircle2 className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />;
    case 'strategy_terminated': return <AlertTriangle className="w-3 h-3 text-red-500 flex-shrink-0 mt-0.5" />;
    case 'strategy_paused': return <Pause className="w-3 h-3 text-yellow-500 flex-shrink-0 mt-0.5" />;
    case 'strategy_resumed': return <Play className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />;
    case 'risk_adjusted': return <Brain className="w-3 h-3 text-violet-500 flex-shrink-0 mt-0.5" />;
    case 'market_monitor_pause': return <Eye className="w-3 h-3 text-amber-500 flex-shrink-0 mt-0.5" />;
    case 'market_monitor_resume': return <Eye className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />;
    case 'orchestrator_decision': return <Activity className="w-3 h-3 text-blue-400 flex-shrink-0 mt-0.5" />;
    case 'circuit_breaker': return <Shield className="w-3 h-3 text-red-600 flex-shrink-0 mt-0.5" />;
    case 'defensive_close': return <Shield className="w-3 h-3 text-red-500 flex-shrink-0 mt-0.5" />;
    case 'defensive_reduce': return <Shield className="w-3 h-3 text-orange-500 flex-shrink-0 mt-0.5" />;
    case 'defensive_hold': return <Shield className="w-3 h-3 text-blue-500 flex-shrink-0 mt-0.5" />;
    case 'defensive_scan': return <Eye className="w-3 h-3 text-orange-400 flex-shrink-0 mt-0.5" />;
    case 'defensive_exit': return <CheckCircle2 className="w-3 h-3 text-green-600 flex-shrink-0 mt-0.5" />;
    case 'analyst_synthesis': return <Brain className="w-3 h-3 text-violet-500 flex-shrink-0 mt-0.5" />;
    case 'master_decision': return <Target className="w-3 h-3 text-blue-600 flex-shrink-0 mt-0.5" />;
    case 'scalp_scan': return <Zap className="w-3 h-3 text-orange-500 flex-shrink-0 mt-0.5" />;
    case 'defensive_block': return <Shield className="w-3 h-3 text-gray-500 flex-shrink-0 mt-0.5" />;
    case 'ai_resume': return <Brain className="w-3 h-3 text-green-600 flex-shrink-0 mt-0.5" />;
    case 'ai_stay_paused': return <Brain className="w-3 h-3 text-amber-500 flex-shrink-0 mt-0.5" />;
    case 'ai_monitoring': return <Eye className="w-3 h-3 text-blue-400 flex-shrink-0 mt-0.5" />;
    case 'session_started': return <Rocket className="w-3 h-3 text-blue-500 flex-shrink-0 mt-0.5" />;
    case 'session_stopped': return <Square className="w-3 h-3 text-gray-500 flex-shrink-0 mt-0.5" />;
    case 'session_paused': return <Pause className="w-3 h-3 text-yellow-500 flex-shrink-0 mt-0.5" />;
    case 'session_resumed': return <Play className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />;
    // 系统告警相关
    case 'system_alert': return <AlertTriangle className="w-3 h-3 text-red-600 flex-shrink-0 mt-0.5 animate-pulse" />;
    case 'data_warning': return <AlertTriangle className="w-3 h-3 text-amber-500 flex-shrink-0 mt-0.5" />;
    case 'data_incomplete': return <AlertTriangle className="w-3 h-3 text-red-500 flex-shrink-0 mt-0.5" />;
    case 'data_gate_block': return <Shield className="w-3 h-3 text-red-600 flex-shrink-0 mt-0.5" />;
    case 'ai_warning': return <Brain className="w-3 h-3 text-amber-500 flex-shrink-0 mt-0.5" />;
    case 'ai_audit_pass': return <CheckCircle2 className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />;
    case 'ai_audit_warning': return <AlertTriangle className="w-3 h-3 text-amber-500 flex-shrink-0 mt-0.5" />;
    case 'ai_audit_reject': return <AlertTriangle className="w-3 h-3 text-red-600 flex-shrink-0 mt-0.5 animate-pulse" />;
    case 'mental_frozen_block': return <Shield className="w-3 h-3 text-red-600 flex-shrink-0 mt-0.5 animate-pulse" />;
    case 'trade_skip': return <CircleDot className="w-3 h-3 text-amber-500 flex-shrink-0 mt-0.5" />;
    case 'recovery_complete': return <CheckCircle2 className="w-3 h-3 text-green-500 flex-shrink-0 mt-0.5" />;
    default: return <CircleDot className="w-3 h-3 text-gray-400 flex-shrink-0 mt-0.5" />;
  }
});

function formatDuration(startIso: string): string {
  const ms = Date.now() - (parseUTC(startIso)?.getTime() || Date.now());
  const hours = Math.floor(ms / 3600000);
  const mins = Math.floor((ms % 3600000) / 60000);
  if (hours > 24) return `${Math.floor(hours / 24)}天${hours % 24}时`;
  if (hours > 0) return `${hours}时${mins}分`;
  return `${mins}分`;
}
