/**
 * 模拟交易面板 — Paper Trading Panel
 * 虚拟资金管理 + 持仓监控 + 订单历史 + 交易统计
 */
import { useState, useEffect, useCallback, useRef, useMemo }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  RotateCcw,
  Play,
  RefreshCw,
  AlertTriangle,
  DollarSign,
  BarChart3,
  ShieldCheck,
  XCircle,
  Loader2,
  ArrowUpRight,
  ArrowDownRight,
  Banknote,
  Target,
  Activity,
  Pencil,
  Clock,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '../ui/dialog';
import { formatPrice, formatSize } from '@/lib/priceFormat';
import { fmtShortDateTime } from '@/lib/utils';
import { getCloseReasonLabel, getCloseReasonColorClass } from '@/lib/closeReasonLabels';
import { MidLongThesisPanel } from './MidLongThesisPanel';

/** FastAPI 的 detail 可能是 string 或校验错误数组 */
function formatFastApiDetail(detail: unknown): string {
  if (detail == null || detail === '') return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { msg?: string }) => item?.msg || JSON.stringify(item))
      .filter(Boolean)
      .join('；');
  }
  return String(detail);
}

// ── Types ──

interface PaperBalance {
  account_id: number;
  initial_balance: number;
  total_equity: number;
  available_balance: number;
  frozen_margin: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_fee_paid: number;
  return_pct: number;
  last_reset_at: string | null;
  updated_at: string | null;
}

interface PaperPosition {
  id: number;
  account_id: number;
  symbol: string;
  side: string;
  size: number;
  entry_price: number;
  mark_price: number;
  leverage: number;
  margin: number;
  unrealized_pnl: number;
  pnl_pct: number;
  liquidation_price: number;
  tp_price: number | null;
  sl_price: number | null;
  trailing_stop_price: number | null;
  status: string;
  close_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
  // 虚拟子仓位字段
  trade_nature: string | null;
  expected_hold_hours: number | null;
  peak_unrealized_pnl?: number | null;
  peak_pnl_pct?: number | null;
  health_score?: number | null;
  health_regime?: string | null;
  exit_state?: {
    nature_staged_tp?: {
      triggered_stages?: number[];
      peak_pnl_pct?: number;
      trailing_active?: boolean;
      trailing_sl_price?: number | null;
    };
  } | null;
  hold_age_hours?: number | null;
  max_hold_hours?: number | null;
  hold_remaining_hours?: number | null;
  hold_progress_pct?: number | null;
  hold_expired?: boolean;
  hold_near_timeout?: boolean;
  hold_ai_extended?: boolean;
  review_hold_hours?: number | null;
  absolute_cap_hours?: number | null;
  extendable_hours?: number | null;
  extend_step_hours_min?: number | null;
  extend_step_hours_max?: number | null;
  reduce_count: number;
  last_reduce_at: string | null;
  // 兼容旧字段
  timeframe_tier?: string;
  add_count?: number;
  dca_count?: number;
  strategy_id?: string;
}

interface PaperOrder {
  id: number;
  account_id: number;
  strategy_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  price: number | null;
  quantity: number;
  filled_quantity: number;
  filled_price: number | null;
  entry_price?: number | null;
  leverage: number;
  tp_price: number | null;
  sl_price: number | null;
  fee: number | null;
  pnl: number | null;
  close_reason: string | null;
  status: string;
  created_at: string | null;
  filled_at: string | null;
}

interface PaperSummary {
  total_orders: number;
  total_closes: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number;
  total_fees: number;
  return_pct: number;
  max_drawdown_pct: number;
  open_losing?: number;
  open_winning?: number;
}

// ── Main Component ──

export default function PaperTradingPanel() {
  const [accountId, setAccountId] = useState<number | null>(null);
  const [accounts, setAccounts] = useState<{ id: number; name: string; trading_mode: string }[]>([]);
  // [2026-07-12 修复] 账户列表专属的加载/错误状态。之前 accounts 初始为 []，
  // loadAccounts() 还没返回时就会被空数组渲染成"暂无模拟账户"的假空状态——
  // 后端一旦变慢(哪怕只是正常的几秒)，用户看到的就是"数据没了"，而不是"在加载"。
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState(false);
  // [2026-07-12 修复] 同样的"假空状态"问题也出现在余额加载上：balance 接口只要一次
  // 超时/网络抖动/DB瞬时报错(非404)，之前会被直接 setBalance(null)，页面就误判成
  // "账户从未初始化"，弹出"开启模拟交易"引导页——账户其实一直有数据，只是这次请求没成功。
  // 用 balanceNotFound 严格区分"后端明确返回404=真的没初始化" 和 "其他任何失败=只是没请求到"。
  const [balanceNotFound, setBalanceNotFound] = useState(false);
  const [balanceLoadError, setBalanceLoadError] = useState(false);
  const [balance, setBalance] = useState<PaperBalance | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [closedPositions, setClosedPositions] = useState<PaperPosition[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [summary, setSummary] = useState<PaperSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [closingId, setClosingId] = useState<number | null>(null);
  const [closeQtyMap, setCloseQtyMap] = useState<Record<number, number>>({});
  const [closePctMap, setClosePctMap] = useState<Record<number, number>>({});
  const [orderSectionTab, setOrderSectionTab] = useState<'records' | 'pending'>('records');
  const [recordFilter, setRecordFilter] = useState<'filled' | 'all'>('filled');
  const [fullAutoSessionId, setFullAutoSessionId] = useState<string | null>(null);

  const attachedOrders = useMemo(
    () => orders
      .filter(isAttachedOrder)
      .sort((a, b) => {
        const rank = (s: string) => (s === 'pending' ? 0 : s === 'filled' ? 1 : 2);
        const d = rank(a.status) - rank(b.status);
        if (d !== 0) return d;
        return b.id - a.id;
      }),
    [orders],
  );
  const pendingAttachedCount = useMemo(
    () => attachedOrders.filter(o => o.status === 'pending').length,
    [attachedOrders],
  );
  const historyOrders = useMemo(
    () => orders.filter(o => {
      if (o.status === 'pending') return false;
      if (isAttachedOrder(o) && o.status === 'cancelled') return false;
      return true;
    }),
    [orders],
  );
  const historyRecords = useMemo(() => {
    if (recordFilter === 'filled') {
      return historyOrders.filter(o => o.status === 'filled');
    }
    return historyOrders;
  }, [historyOrders, recordFilter]);

  useEffect(() => {
    if (!positions.length) return;
    setCloseQtyMap(prev => {
      const next = { ...prev };
      let changed = false;
      for (const p of positions) {
        if (next[p.id] !== undefined && next[p.id] > p.size) {
          next[p.id] = p.size;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    setClosePctMap(prev => {
      const next = { ...prev };
      let changed = false;
      for (const p of positions) {
        if (next[p.id] !== undefined && prev[p.id] !== undefined) {
          const qty = closeQtyMap[p.id] ?? p.size;
          const clampedQty = Math.min(qty, p.size);
          const newPct = p.size > 0 ? Math.round(clampedQty / p.size * 100) : 100;
          if (next[p.id] !== newPct) {
            next[p.id] = newPct;
            changed = true;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [positions]);
  const [initAmount, setInitAmount] = useState(100000);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showSetBalanceDialog, setShowSetBalanceDialog] = useState(false);
  const [newBalanceAmount, setNewBalanceAmount] = useState(100000);
  const [settingBalance, setSettingBalance] = useState(false);

  // 新增：多模拟账户管理
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newAccountName, setNewAccountName] = useState('');
  const [newAccountBalance, setNewAccountBalance] = useState(100000);
  const [creating, setCreating] = useState(false);
  const [editingNameId, setEditingNameId] = useState<number | null>(null);
  const [editNameValue, setEditNameValue] = useState('');

  // 加载账户列表
  useEffect(() => { loadAccounts(); }, []);
  useEffect(() => {
    if (accountId) return;
    const paperAccounts = accounts.filter(a => a.trading_mode === 'paper');
    if (paperAccounts.length === 0) return;
    // 优先选中「启用中」的模拟账户，避免默认落到已停用的旧账户（如 id=4）上看不到新会话成交
    const activePaper = paperAccounts.filter(
      (a) => a.is_active === true || a.is_active === 'true'
    );
    const pick = (activePaper.length > 0 ? activePaper : paperAccounts)
      .slice()
      .sort((a, b) => b.id - a.id)[0];
    setAccountId(pick.id);
  }, [accounts, accountId]);

  // 加载数据
  const loadData = useCallback(async (silent = false) => {
    if (!accountId) return;
    if (!silent) setLoading(true);
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      // P5-fix(2026-05-08): cache: 'no-store' 禁用浏览器 HTTP 缓存
      // 否则 15s 轮询拿到的可能是浏览器/CDN 的旧响应，导致 UI 显示陈旧持仓
      const fetchOpts = { signal: controller.signal, cache: 'no-store' as RequestCache };
      const [balRes, posRes, closedRes, ordRes, sumRes] = await Promise.all([
        fetch(`/api/paper/balance/${accountId}`, fetchOpts),
        fetch(`/api/paper/positions/${accountId}?status=open`, fetchOpts),
        fetch(`/api/paper/positions/${accountId}?status=closed`, fetchOpts),
        fetch(`/api/paper/orders/${accountId}?limit=50`, fetchOpts),
        fetch(`/api/paper/summary/${accountId}`, fetchOpts),
      ]);
      clearTimeout(timeout);

      if (balRes.ok) {
        setBalance(await balRes.json());
        setBalanceNotFound(false);
        setBalanceLoadError(false);
      } else if (balRes.status === 404) {
        // 后端明确说"这个账户没有模拟盘记录"，才是真的需要引导用户初始化
        setBalance(null);
        setBalanceNotFound(true);
        setBalanceLoadError(false);
      } else {
        // 5xx/其它错误：不清空 balance，保留上一次已知数据，只标记"这次没刷新成功"
        setBalanceLoadError(true);
      }

      if (posRes.ok) {
        const _posData = await posRes.json();
        setPositions(_posData);
        // P5-fix: 把"当前查的是哪个账户 / 拿到了多少持仓"打到控制台，
        // 方便用户在 DevTools 里看到，避免误以为是"账户脱节"
        if (typeof window !== 'undefined') {
          console.debug(
            `[PaperTrading] account=${accountId} 拉取到 ${_posData.length} 个 open 持仓`,
            _posData.map((p: PaperPosition) => `${p.symbol}/${p.side}(${p.timeframe_tier})`)
          );
        }
      }
      if (closedRes.ok) setClosedPositions(await closedRes.json());
      if (ordRes.ok) setOrders(await ordRes.json());
      if (sumRes.ok) setSummary(await sumRes.json());
    } catch (err) {
      console.error('加载模拟交易数据失败', err);
      // 网络错误/超时：同样不清空 balance，只标记加载失败，避免误判成"未初始化"
      setBalanceLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  const loadDataRef = useRef(loadData);
  loadDataRef.current = loadData;

  useEffect(() => { loadData(); }, [loadData]);

  useEffect(() => {
    if (!accountId) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') loadDataRef.current(true);
    }, 15000);
    return () => clearInterval(timer);
  }, [accountId]);

  useEffect(() => {
    if (!accountId) {
      setFullAutoSessionId(null);
      return;
    }
    const fetchLinkedSession = async () => {
      try {
        const res = await fetch('/api/full-auto/sessions');
        if (!res.ok) return;
        const sessions = await res.json();
        const match = (Array.isArray(sessions) ? sessions : []).find(
          (s: { status?: string; paper_account_id?: number | null; session_id?: string }) =>
            s.status === 'running' && Number(s.paper_account_id) === accountId,
        );
        setFullAutoSessionId(match?.session_id || null);
      } catch {
        /* ignore */
      }
    };
    fetchLinkedSession();
    const timer = setInterval(fetchLinkedSession, 30000);
    return () => clearInterval(timer);
  }, [accountId]);

  // 初始化模拟账户
  const handleInitialize = async () => {
    if (!accountId) return;
    setInitializing(true);
    try {
      const res = await fetch('/api/paper/initialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId, initial_balance: initAmount }),
      });
      if (res.ok) {
        toast.success(`模拟账户已初始化，资金 ${initAmount.toLocaleString()} USDT`);
        await loadData();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || '初始化失败');
      }
    } catch {
      toast.error('网络错误');
    } finally {
      setInitializing(false);
    }
  };

  // 软重置（仅钱包数字）
  const handleResetBalance = async () => {
    if (!accountId) return;
    try {
      const res = await fetch(`/api/paper/reset-balance/${accountId}`, { method: 'POST' });
      if (res.ok) {
        toast.success('钱包已重置（盈亏/手续费归零），持仓和交易对不受影响');
        setShowResetConfirm(false);
        await loadData();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || '重置失败');
      }
    } catch {
      toast.error('网络错误');
    }
  };

  // 硬重置（清空一切）
  const handleResetFull = async () => {
    if (!accountId) return;
    try {
      const res = await fetch(`/api/paper/reset/${accountId}`, { method: 'POST' });
      if (res.ok) {
        toast.success('模拟账户已全部清空（持仓/订单已删除），交易对配置不受影响');
        setShowResetConfirm(false);
        await loadData();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || '重置失败');
      }
    } catch {
      toast.error('网络错误');
    }
  };

  // 修改初始金额
  const handleSetBalance = async () => {
    if (!accountId) return;
    setSettingBalance(true);
    try {
      const res = await fetch('/api/paper/set-balance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId, initial_balance: newBalanceAmount }),
      });
      if (res.ok) {
        toast.success(`初始金额已修改为 ${newBalanceAmount.toLocaleString()} USDT`);
        setShowSetBalanceDialog(false);
        await loadData();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(formatFastApiDetail(err.detail) || '修改金额失败');
      }
    } catch {
      toast.error('网络错误');
    } finally {
      setSettingBalance(false);
    }
  };

  // 创建新的模拟账户
  const handleCreateAccount = async () => {
    if (!newAccountName.trim()) { toast.error('请输入账户名称'); return; }
    setCreating(true);
    try {
      const res = await fetch('/api/account/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newAccountName.trim(), trading_mode: 'paper', account_type: 'PAPER', model: '', api_key: '', base_url: '', initial_capital: newAccountBalance }),
      });
      if (res.ok) {
        const data = await res.json();
        toast.success(`模拟账户「${newAccountName}」已创建`);
        // Initialize paper balance
        await fetch('/api/paper/initialize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ account_id: data.id, initial_balance: newAccountBalance }),
        });
        setShowCreateDialog(false);
        setNewAccountName('');
        await loadAccounts();
        setAccountId(data.id);
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || '创建失败');
      }
    } catch { toast.error('网络错误'); }
    finally { setCreating(false); }
  };

  // 删除模拟账户
  const handleDeleteAccount = async (id: number) => {
    const acc = accounts.find(a => a.id === id);
    if (!confirm(`确定删除模拟账户「${acc?.name || id}」？此操作不可恢复。`)) return;
    try {
      const res = await fetch(`/api/account/${id}`, { method: 'DELETE' });
      if (res.ok) {
        toast.success(`模拟账户已删除`);
        if (accountId === id) {
          setAccountId(null);
          setBalance(null);
          setPositions([]);
        }
        await loadAccounts();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || '删除失败');
      }
    } catch { toast.error('网络错误'); }
  };

  // 重命名模拟账户
  const handleRenameAccount = async (id: number) => {
    if (!editNameValue.trim()) { setEditingNameId(null); return; }
    try {
      const res = await fetch(`/api/account/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: editNameValue.trim() }),
      });
      if (res.ok) {
        toast.success('名称已更新');
        setEditingNameId(null);
        await loadAccounts();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || '重命名失败');
      }
    } catch { toast.error('网络错误'); }
  };

  const loadAccounts = async () => {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      const res = await fetch('/api/account/list', { signal: controller.signal, cache: 'no-store' });
      clearTimeout(timeout);
      if (res.ok) {
        const data = await res.json();
        setAccounts(data);
        setAccountsError(false);
      } else {
        setAccountsError(true);
      }
    } catch {
      // 网络错误/超时：明确标记为"加载失败"，不要让 accounts 悄悄保持空数组
      // 从而被误判成"用户确实没有账户"。
      setAccountsError(true);
    } finally {
      setAccountsLoading(false);
    }
  };

  // 平仓（支持部分平仓）
  const handleClose = async (symbol: string, side: string, qty?: number) => {
    if (!accountId) return;
    const pos = positions.find(p => p.symbol === symbol && p.side === side);
    if (pos) setClosingId(pos.id);
    try {
      const body: Record<string, any> = { account_id: accountId, symbol, side };
      if (qty !== undefined && qty > 0) body.quantity = qty;
      const res = await fetch('/api/paper/close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        const pnl = data.result?.pnl ?? 0;
        const closedFully = data.result?.closed_fully !== false;
        const label = closedFully ? '已平仓' : '已部分平仓';
        toast.success(`${label} ${symbol} ${side} | PnL: ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT`);
        if (pos) {
          setCloseQtyMap(prev => { const m = { ...prev }; delete m[pos.id]; return m; });
          setClosePctMap(prev => { const m = { ...prev }; delete m[pos.id]; return m; });
        }
        await loadData();
      } else {
        toast.error('平仓失败');
      }
    } catch {
      toast.error('网络错误');
    } finally {
      setClosingId(null);
    }
  };

  // ── 模拟账户选项卡栏（始终显示）──
  const paperAccounts = accounts.filter(a => a.trading_mode === 'paper');

  const renderTabBar = () => (
    <div className="flex items-center gap-2 px-4 py-2 bg-gray-50 dark:bg-gray-900 border-b dark:border-gray-800 overflow-x-auto">
      <Wallet className="w-4 h-4 text-amber-500 flex-shrink-0" />
      <span className="text-xs font-semibold text-muted-foreground flex-shrink-0">模拟账户:</span>
      {paperAccounts.map(a => {
        const isActive = a.id === accountId;
        return (
          <div key={a.id} className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all flex-shrink-0 ${
            isActive
              ? 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 ring-1 ring-amber-400'
              : 'bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-400'
          }`}>
            {editingNameId === a.id ? (
              <input
                className="w-20 h-5 px-1 text-xs border rounded bg-white dark:bg-gray-700"
                value={editNameValue}
                onChange={e => setEditNameValue(e.target.value)}
                onBlur={() => handleRenameAccount(a.id)}
                onKeyDown={e => { if (e.key === 'Enter') handleRenameAccount(a.id); if (e.key === 'Escape') setEditingNameId(null); }}
                autoFocus
                onClick={e => e.stopPropagation()}
              />
            ) : (
              <button
                onClick={() => setAccountId(a.id)}
                onDoubleClick={() => { setEditingNameId(a.id); setEditNameValue(a.name); }}
                className="max-w-[120px] truncate font-medium"
                title="单击切换 / 双击重命名"
              >
                {a.name}
              </button>
            )}
            <button
              onClick={e => { e.stopPropagation(); handleDeleteAccount(a.id); }}
              className="ml-0.5 p-0.5 rounded-full hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-600 flex-shrink-0"
              title="删除账户"
            >
              <XCircle className="w-3 h-3" />
            </button>
          </div>
        );
      })}
      <button
        onClick={() => { setShowCreateDialog(true); setNewAccountName(''); setNewAccountBalance(100000); }}
        className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-white dark:bg-gray-800 hover:bg-amber-100 dark:hover:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-dashed border-amber-300 dark:border-amber-700 flex-shrink-0"
      >
        <span className="text-base leading-none">+</span>
        新建
      </button>
      <button onClick={loadAccounts} className="p-1.5 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 flex-shrink-0 ml-auto" title="刷新">
        <RefreshCw className="w-3.5 h-3.5 text-muted-foreground" />
      </button>
    </div>
  );

  // ── 创建模拟账户对话框 ──
  const renderCreateDialog = () => (
    <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
      <DialogContent className="max-w-sm">
        <DialogHeader><DialogTitle>新建模拟账户</DialogTitle></DialogHeader>
        <div className="space-y-4 py-4">
          <div>
            <label className="text-xs font-medium">账户名称</label>
            <input
              className="w-full mt-1 px-3 py-2 text-sm border rounded-md bg-background"
              placeholder="例如：BTC V4-Flash 测试"
              value={newAccountName}
              onChange={e => setNewAccountName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateAccount()}
            />
          </div>
          <div>
            <label className="text-xs font-medium">初始资金 (USDT)</label>
            <div className="flex gap-2 mt-1">
              {[10000, 50000, 100000, 500000].map(amt => (
                <button
                  key={amt}
                  onClick={() => setNewAccountBalance(amt)}
                  className={`flex-1 px-2 py-1.5 rounded text-xs font-medium border ${
                    newAccountBalance === amt ? 'bg-amber-500 text-white border-amber-500' : 'bg-background border-input'
                  }`}
                >
                  {(amt / 1000).toFixed(0)}K
                </button>
              ))}
            </div>
            <input
              className="w-full mt-2 px-3 py-2 text-sm border rounded-md bg-background"
              type="number"
              value={newAccountBalance}
              onChange={e => setNewAccountBalance(Number(e.target.value))}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setShowCreateDialog(false)}>取消</Button>
          <Button onClick={handleCreateAccount} disabled={creating || !newAccountName.trim()}>
            {creating ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : null}
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  // ── 账户列表仍在加载：显示加载中，不要误判为"没有账户" ──
  if (accountsLoading) {
    return (
      <div className="p-0">
        {renderTabBar()}
        <div className="p-12 text-center">
          <Loader2 className="w-10 h-10 mx-auto mb-4 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">正在加载模拟账户...</p>
        </div>
      </div>
    );
  }

  // ── 账户列表加载失败：明确提示网络/后端问题，而不是"暂无账户" ──
  if (accountsError && paperAccounts.length === 0) {
    return (
      <div className="p-0">
        {renderTabBar()}
        <div className="p-12 text-center">
          <AlertTriangle className="w-16 h-16 mx-auto mb-4 text-red-400" />
          <h3 className="text-lg font-bold mb-2">账户列表加载失败</h3>
          <p className="text-sm text-muted-foreground mb-6">
            无法连接后端或后端响应超时，这不代表账户数据丢失，请重试。
          </p>
          <Button onClick={() => { setAccountsLoading(true); loadAccounts(); }}>
            <RefreshCw className="w-4 h-4 mr-1" />
            重新加载
          </Button>
        </div>
      </div>
    );
  }

  // ── 无模拟账户：空状态（此时已确认加载成功且真的没有账户）──
  if (paperAccounts.length === 0 && !loading) {
    return (
      <div className="p-0">
        {renderTabBar()}
        {renderCreateDialog()}
        <div className="p-12 text-center">
          <Wallet className="w-16 h-16 mx-auto mb-4 text-amber-200 dark:text-amber-800" />
          <h3 className="text-lg font-bold mb-2">暂无模拟账户</h3>
          <p className="text-sm text-muted-foreground mb-6">
            模拟账户用于纸上交易测试，与交易员（API账户）完全独立。
          </p>
          <Button
            onClick={() => { setShowCreateDialog(true); setNewAccountName(''); setNewAccountBalance(100000); }}
            className="bg-amber-500 hover:bg-amber-600 text-white"
          >
            创建第一个模拟账户
          </Button>
        </div>
      </div>
    );
  }

  // ── 余额加载失败(非404)：明确提示网络/后端问题，不要误判成"未初始化" ──
  if (!balance && balanceLoadError && !loading) {
    return (
      <div className="p-0">
        {renderTabBar()}
        <div className="p-12 text-center">
          <AlertTriangle className="w-16 h-16 mx-auto mb-4 text-red-400" />
          <h3 className="text-lg font-bold mb-2">账户数据加载失败</h3>
          <p className="text-sm text-muted-foreground mb-6">
            该账户之前是有数据的，这次只是网络或后端响应超时，不代表账户被清空，请重试。
          </p>
          <Button onClick={() => loadData()}>
            <RefreshCw className="w-4 h-4 mr-1" />
            重新加载
          </Button>
        </div>
      </div>
    );
  }

  // ── 兜底：还没收到任何明确结果(既不是成功/也不是404/也不是失败)，说明还在路上 ──
  // 避免落到下面假设 balance 一定非空的主看板渲染逻辑里直接崩溃。
  if (!balance && !balanceNotFound && !balanceLoadError) {
    return (
      <div className="p-0">
        {renderTabBar()}
        <div className="p-12 text-center">
          <Loader2 className="w-10 h-10 mx-auto mb-4 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">正在加载账户数据...</p>
        </div>
      </div>
    );
  }

  // ── 未初始化状态（后端已明确返回404，确认这个账户真的从没初始化过）──
  if (!balance && balanceNotFound && !loading) {
    return (
      <div className="p-0">
        {renderTabBar()}
        {renderCreateDialog()}
        <div className="p-6">
        <div className="max-w-lg mx-auto">
          <Card className="border-2 border-dashed border-amber-300 dark:border-amber-700">
            <CardHeader className="text-center pb-2">
              <div className="mx-auto w-16 h-16 rounded-2xl bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center mb-3">
                <Wallet className="w-8 h-8 text-white" />
              </div>
              <CardTitle className="text-xl">开启模拟交易</CardTitle>
              <p className="text-sm text-muted-foreground mt-1">
                使用虚拟资金进行交易测试，零风险验证策略
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* 账户选择 */}
              <div>
                <label className="text-xs font-medium text-muted-foreground">选择账户</label>
                <select
                  className="w-full mt-1 rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={accountId ?? ''}
                  onChange={(e) => setAccountId(Number(e.target.value))}
                >
                  {accounts.map(a => (
                    <option key={a.id} value={a.id}>{a.name} (ID: {a.id})</option>
                  ))}
                </select>
              </div>

              {/* 初始资金 */}
              <div>
                <label className="text-xs font-medium text-muted-foreground">初始虚拟资金 (USDT)</label>
                <div className="flex gap-2 mt-1">
                  {[10000, 50000, 100000, 500000].map(amt => (
                    <button
                      key={amt}
                      onClick={() => setInitAmount(amt)}
                      className={`flex-1 px-2 py-1.5 rounded text-xs font-medium border transition-colors ${
                        initAmount === amt
                          ? 'bg-amber-500 text-white border-amber-500'
                          : 'bg-background border-input hover:bg-accent'
                      }`}
                    >
                      {(amt / 1000).toFixed(0)}K
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  value={initAmount}
                  onChange={(e) => setInitAmount(Number(e.target.value))}
                  className="w-full mt-2 rounded-md border border-input bg-background px-3 py-2 text-sm"
                  min={100}
                />
              </div>

              <Button
                className="w-full bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white"
                onClick={handleInitialize}
                disabled={initializing || !accountId}
              >
                {initializing ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Play className="w-4 h-4 mr-2" />}
                启动模拟交易
              </Button>

              <div className="bg-blue-50 dark:bg-blue-950/30 rounded-lg p-3 text-xs text-blue-700 dark:text-blue-300 space-y-1">
                <p className="font-medium">关于模拟交易</p>
                <ul className="list-disc pl-4 space-y-0.5">
                  <li>使用真实市场价格，虚拟资金交易</li>
                  <li>完整模拟：杠杆、手续费(0.04%)、滑点、爆仓</li>
                  <li>AI策略可在模拟模式下自动运行和学习</li>
                  <li>不会触发任何真实交易所 API</li>
                </ul>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
      </div>
    );
  }

  // ── 主面板 ──
  return (
    <div className="p-0">
      {renderTabBar()}
      {renderCreateDialog()}
      <div className="p-6 space-y-4">
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center">
            <Banknote className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold flex items-center gap-2">
              模拟交易
              <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-700 text-[10px]">
                PAPER
              </Badge>
            </h1>
            <p className="text-xs text-muted-foreground">虚拟资金 · 真实价格 · 零风险测试</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={loadData} disabled={loading}>
            <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowSetBalanceDialog(true)}
            title="修改初始金额"
          >
            <Pencil className="w-3.5 h-3.5 mr-1.5" />
            金额
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="text-red-600 border-red-200 hover:bg-red-50"
            onClick={() => setShowResetConfirm(true)}
          >
            <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
            重置
          </Button>
        </div>
      </div>

      {fullAutoSessionId && (
        <MidLongThesisPanel sessionId={fullAutoSessionId} refreshSec={30} defaultCollapsed />
      )}

      {/* 修改金额 Dialog */}
      <Dialog open={showSetBalanceDialog} onOpenChange={setShowSetBalanceDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <DollarSign className="w-5 h-5 text-amber-500" />
              修改初始金额
            </DialogTitle>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <p className="text-sm text-muted-foreground">
              修改模拟账户的初始资金。仅允许在无持仓时修改。
            </p>
            <div>
              <label className="text-xs font-medium text-muted-foreground">新初始金额 (USDT)</label>
              <div className="flex gap-2 mt-1.5">
                {[10000, 50000, 100000, 500000].map(amt => (
                  <button
                    key={amt}
                    onClick={() => setNewBalanceAmount(amt)}
                    className={`flex-1 px-2 py-1.5 rounded text-xs font-medium border transition-colors ${
                      newBalanceAmount === amt
                        ? 'bg-amber-500 text-white border-amber-500'
                        : 'bg-background border-input hover:bg-accent'
                    }`}
                  >
                    {(amt / 1000).toFixed(0)}K
                  </button>
                ))}
              </div>
              <input
                type="number"
                value={newBalanceAmount}
                onChange={(e) => setNewBalanceAmount(Number(e.target.value))}
                className="w-full mt-2 rounded-md border border-input bg-background px-3 py-2 text-sm"
                min={100}
              />
            </div>
            {balance && (
              <p className="text-xs text-muted-foreground">
                当前初始金额: {balance.initial_balance.toLocaleString()} USDT
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowSetBalanceDialog(false)}>取消</Button>
            <Button
              onClick={handleSetBalance}
              disabled={settingBalance || newBalanceAmount < 100}
              className="bg-amber-500 hover:bg-amber-600 text-white"
            >
              {settingBalance ? <Loader2 className="w-4 h-4 animate-spin mr-1.5" /> : null}
              确认修改
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重置确认 — 提供两种模式 */}
      {showResetConfirm && (
        <Card className="border-amber-300 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20">
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-amber-500" />
              <p className="text-sm font-medium">选择重置方式</p>
              <Button size="sm" variant="ghost" className="ml-auto text-xs" onClick={() => setShowResetConfirm(false)}>取消</Button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <button
                onClick={handleResetBalance}
                className="p-3 rounded-lg border-2 border-amber-300 dark:border-amber-700 bg-white dark:bg-gray-900 hover:bg-amber-50 dark:hover:bg-amber-950/30 text-left transition-colors"
              >
                <p className="text-sm font-semibold text-amber-700 dark:text-amber-300">重置钱包</p>
                <p className="text-[11px] text-muted-foreground mt-1">仅归零盈亏和手续费</p>
                <p className="text-[11px] text-green-600 dark:text-green-400 mt-0.5">保留持仓 · 保留订单 · 保留交易对</p>
              </button>
              <button
                onClick={handleResetFull}
                className="p-3 rounded-lg border-2 border-red-300 dark:border-red-700 bg-white dark:bg-gray-900 hover:bg-red-50 dark:hover:bg-red-950/30 text-left transition-colors"
              >
                <p className="text-sm font-semibold text-red-700 dark:text-red-300">全部清空</p>
                <p className="text-[11px] text-muted-foreground mt-1">清除全部持仓和订单，恢复初始资金</p>
                <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-0.5">交易对配置不受影响</p>
              </button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 资金概览 */}
      {balance && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <StatCard
            label="总权益"
            value={`$${balance.total_equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            icon={<DollarSign className="w-4 h-4" />}
            change={balance.return_pct}
            color="blue"
          />
          <StatCard
            label="可用余额"
            value={`$${balance.available_balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            icon={<Wallet className="w-4 h-4" />}
            color="green"
          />
          <StatCard
            label="冻结保证金"
            value={`$${balance.frozen_margin.toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            icon={<ShieldCheck className="w-4 h-4" />}
            color="amber"
          />
          <StatCard
            label="未实现盈亏"
            value={`${balance.unrealized_pnl >= 0 ? '+' : '-'}$${Math.abs(balance.unrealized_pnl).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            icon={balance.unrealized_pnl >= 0 ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
            color={balance.unrealized_pnl >= 0 ? 'green' : 'red'}
          />
          <StatCard
            label="已实现盈亏"
            value={`${balance.realized_pnl >= 0 ? '+' : '-'}$${Math.abs(balance.realized_pnl).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
            icon={<BarChart3 className="w-4 h-4" />}
            change={balance.realized_pnl !== 0 ? (balance.realized_pnl / balance.initial_balance * 100) : undefined}
            color={balance.realized_pnl >= 0 ? 'green' : 'red'}
          />
        </div>
      )}

      {/* 统计摘要 */}
      {summary && (summary.total_closes > 0 || (summary.wins + summary.losses) > 0) && (
        <div>
          {summary.last_reset_at && (
            <p className="text-[10px] text-muted-foreground mb-1.5">
              统计区间：{new Date(summary.last_reset_at).toLocaleString()} 至今
            </p>
          )}
          <div className="grid grid-cols-3 md:grid-cols-7 gap-2">
          {[
            { label: '胜/败', value: `${summary.wins}W / ${summary.losses}L` },
            { label: '胜率', value: `${(summary.win_rate * 100).toFixed(1)}%`,
              color: summary.win_rate >= 0.5 ? 'text-green-600' : 'text-red-600' },
            { label: '盈亏比', value: summary.profit_factor.toFixed(2) },
            { label: '总盈亏', value: `${summary.total_pnl >= 0 ? '+' : '-'}$${Math.abs(summary.total_pnl).toFixed(2)}`,
              color: summary.total_pnl >= 0 ? 'text-green-600' : 'text-red-600' },
            { label: '收益率', value: `${summary.return_pct.toFixed(2)}%`,
              color: summary.return_pct >= 0 ? 'text-green-600' : 'text-red-600' },
            { label: '手续费', value: `$${summary.total_fees.toFixed(2)}` },
            { label: '持仓中亏损', value: `${summary.open_losing ?? 0}笔`,
              color: (summary.open_losing ?? 0) > 0 ? 'text-red-600' : '' },
          ].map(item => (
            <div key={item.label} className="bg-muted/50 rounded-lg px-3 py-2 text-center">
              <p className="text-[10px] text-muted-foreground">{item.label}</p>
              <p className={`text-sm font-semibold ${'color' in item ? (item as any).color : ''}`}>{item.value}</p>
            </div>
          ))}
        </div>
        </div>
      )}

      {/* 持仓列表 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Target className="w-4 h-4" />
            当前持仓
            {positions.length > 0 && (
              <Badge variant="secondary" className="text-[10px]">{positions.length}</Badge>
            )}
            {/* 按 symbol 统计子仓数量 */}
            {(() => {
              const symSet = new Set(positions.map(p => p.symbol));
              return symSet.size > 0 && symSet.size < positions.length ? (
                <span className="text-[10px] text-muted-foreground ml-1">
                  ({symSet.size}币种 · {positions.length}子仓)
                </span>
              ) : null;
            })()}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {positions.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-6">暂无持仓</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 pr-3">币种</th>
                    <th className="text-left py-2 pr-3">子仓类型</th>
                    <th className="text-left py-2 pr-3">方向</th>
                    <th className="text-right py-2 pr-3">数量</th>
                    <th className="text-right py-2 pr-3">入场价</th>
                    <th className="text-right py-2 pr-3">标记价</th>
                    <th className="text-right py-2 pr-3">杠杆</th>
                    <th className="text-right py-2 pr-3">保证金</th>
                    <th className="text-right py-2 pr-3">浮盈(USDT)</th>
                    <th className="text-right py-2 pr-3">浮盈(%)</th>
                    <th className="text-right py-2 pr-3">爆仓价</th>
                    <th className="text-right py-2 pr-3">TP / SL</th>
                    <th className="text-right py-2 min-w-[260px]">平仓操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const grouped = positions.reduce<Record<string, PaperPosition[]>>((acc, p) => {
                      (acc[p.symbol] ??= []).push(p);
                      return acc;
                    }, {});
                    const sortedSymbols = Object.keys(grouped).sort();

                    return sortedSymbols.flatMap((sym, symIdx) => {
                      const group = grouped[sym];
                      const isMulti = group.length > 1;

                      return group.map((p, posIdx) => {
                    const baseSymbol = (p.symbol || '').replace(/\/?USDT$/i, '') || p.symbol;
                    const curPct = closePctMap[p.id] ?? 100;
                    const curQty = closeQtyMap[p.id] ?? p.size;
                    const SNAP_POINTS = [30, 50, 70, 100] as const;
                    const handlePctChange = (pct: number) => {
                      const clamped = Math.max(1, Math.min(100, pct));
                      setClosePctMap(prev => ({ ...prev, [p.id]: clamped }));
                      setCloseQtyMap(prev => ({ ...prev, [p.id]: +(p.size * clamped / 100).toFixed(8) }));
                    };
                    const handleQtyInput = (val: number) => {
                      const clamped = Math.max(0, Math.min(p.size, val));
                      setCloseQtyMap(prev => ({ ...prev, [p.id]: clamped }));
                      setClosePctMap(prev => ({ ...prev, [p.id]: p.size > 0 ? Math.round(clamped / p.size * 100) : 100 }));
                    };

                    const natureKey = p.trade_nature || p.timeframe_tier || 'swing';
                    const tierKey = p.timeframe_tier || '';
                    const natureConfig: Record<string, { label: string; bg: string; text: string; border: string; darkBg: string; darkText: string }> = {
                      trend_follow: { label: '趋势仓', bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-300', darkBg: 'dark:bg-blue-950', darkText: 'dark:text-blue-300' },
                      swing:        { label: '波段仓', bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-300', darkBg: 'dark:bg-amber-950', darkText: 'dark:text-amber-300' },
                      intraday:     { label: '日内仓', bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-300', darkBg: 'dark:bg-purple-950', darkText: 'dark:text-purple-300' },
                      position:     { label: '趋势仓', bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-300', darkBg: 'dark:bg-blue-950', darkText: 'dark:text-blue-300' },
                      scalp:        { label: '短线', bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-300', darkBg: 'dark:bg-orange-950', darkText: 'dark:text-orange-300' },
                    };
                    // timeframe_tier → 周期标签及颜色
                    const tierConfig: Record<string, { label: string; bg: string; text: string; border: string; darkBg: string; darkText: string }> = {
                      long:  { label: '长线', bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-300', darkBg: 'dark:bg-emerald-950', darkText: 'dark:text-emerald-300' },
                      mid:   { label: '波段', bg: 'bg-sky-50',     text: 'text-sky-700',     border: 'border-sky-300',     darkBg: 'dark:bg-sky-950',     darkText: 'dark:text-sky-300' },
                      short: { label: '短线', bg: 'bg-orange-50',  text: 'text-orange-700',  border: 'border-orange-300',  darkBg: 'dark:bg-orange-950',  darkText: 'dark:text-orange-300' },
                    };
                    const nc = natureConfig[p.trade_nature || ''];
                    const tc = tierConfig[tierKey];
                    // 降级：只有 timeframe_tier 没有 trade_nature 时用旧映射
                    const fallbackLabels: Record<string, string> = {
                      short: '短线(日内)',
                      mid: '波段(1–3天)',
                      long: '长线趋势',
                    };
                    const natureLabel = nc?.label || fallbackLabels[natureKey] || '波段仓';
                    // 持仓时长：已持 / AI批准上限（到 tier 复审点会触发 AI 评估，非自动强平）
                    // 超过 24h 时保留「X天Yh」，避免 71h 被四舍五入成 3.0天 与上限混淆
                    const fmtHours = (h: number) => {
                      if (!Number.isFinite(h) || h < 0) return '—';
                      if (h < 24) return `${h.toFixed(1)}h`;
                      const wholeDays = Math.floor(h / 24);
                      const remH = h - wholeDays * 24;
                      if (remH >= 0.05) {
                        const remText = remH >= 1 ? `${Math.round(remH)}h` : `${remH.toFixed(1)}h`;
                        return `${wholeDays}天${remText}`;
                      }
                      return `${wholeDays}天`;
                    };
                    const holdAge = p.hold_age_hours ?? null;
                    const holdMax = p.max_hold_hours ?? p.expected_hold_hours ?? null;
                    const holdRemain = p.hold_remaining_hours ?? null;
                    const holdProgress = p.hold_progress_pct ?? null;
                    const holdHint =
                      holdAge != null && holdMax != null
                        ? `已持${fmtHours(holdAge)}/${fmtHours(holdMax)}`
                        : p.expected_hold_hours
                          ? `预期~${fmtHours(p.expected_hold_hours)}`
                          : null;
                    const holdUrgent = p.hold_expired || p.hold_near_timeout;
                    const extendMin = p.extend_step_hours_min ?? 4;
                    const extendMax = p.extend_step_hours_max ?? 16;
                    const extendableH = p.extendable_hours ?? null;
                    const absCapH = p.absolute_cap_hours ?? null;
                    const holdExtendRange =
                      extendableH != null && extendableH > 0.05
                        ? `可延+${extendMin}~${extendMax}h${
                            absCapH != null ? `/至${fmtHours(absCapH)}` : ''
                          }`
                        : '';
                    const holdExtendHint = (() => {
                      if (!holdUrgent) return '';
                      if (p.hold_expired) {
                        return holdExtendRange
                          ? ` · 待AI平/延 · ${holdExtendRange}`
                          : ' · 待AI平/延(已达延长期上限)';
                      }
                      if (p.hold_near_timeout) {
                        return holdExtendRange
                          ? ` · 待AI复审 · ${holdExtendRange}`
                          : ' · 待AI复审';
                      }
                      return '';
                    })();
                    const healthScore = typeof p.health_score === 'number' ? p.health_score : null;
                    const healthRegime = p.health_regime || '';
                    const healthTone =
                      healthScore == null ? 'text-muted-foreground'
                        : healthScore >= 70 ? 'text-green-600 dark:text-green-400'
                          : healthScore >= 45 ? 'text-amber-600 dark:text-amber-400'
                            : 'text-red-600 dark:text-red-400';
                    const stagedState = p.exit_state?.nature_staged_tp;
                    const stageCount = stagedState?.triggered_stages?.length ?? 0;
                    const trailingActive = !!stagedState?.trailing_active;
                    const peakPct = typeof p.peak_pnl_pct === 'number' ? p.peak_pnl_pct : null;

                    const isFirstInGroup = posIdx === 0;
                    const groupBorderClass = isMulti && isFirstInGroup && symIdx > 0 ? 'border-t-2 border-t-muted-foreground/20' : '';

                    return (
                    <tr key={p.id} className={`border-b last:border-0 hover:bg-muted/30 align-top ${groupBorderClass}`}>
                      <td className="py-2 pr-3 font-medium">
                        {isMulti && !isFirstInGroup ? (
                          <span className="text-muted-foreground pl-3">└</span>
                        ) : (
                          <span>{p.symbol}</span>
                        )}
                      </td>
                      <td className="py-2 pr-3">
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1 flex-wrap">
                            {/* 周期标签（长线/中线/短线）*/}
                            {tc ? (
                              <Badge
                                variant="outline"
                                className={`text-[10px] px-1 py-0 ${tc.bg} ${tc.text} ${tc.border} ${tc.darkBg} ${tc.darkText}`}
                              >
                                {tc.label}
                              </Badge>
                            ) : null}
                            {/* 策略类型标签（趋势仓/波段仓/日内仓）*/}
                            <Badge
                              variant="outline"
                              className={`text-[10px] ${nc ? `${nc.bg} ${nc.text} ${nc.border} ${nc.darkBg} ${nc.darkText}` : ''}`}
                            >
                              {natureLabel}
                              {(p.add_count ?? 0) > 0 && (
                                <span className="ml-1 opacity-70">+{p.add_count}</span>
                              )}
                              {(p.dca_count ?? 0) > 0 && (
                                <span className="ml-1 opacity-70">↓{p.dca_count}</span>
                              )}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-1">
                            {p.reduce_count > 0 && (
                              <span className="text-[9px] text-muted-foreground">已减{p.reduce_count}次</span>
                            )}
                            {holdHint && (
                              <span
                                className={`text-[9px] ${
                                  holdUrgent
                                    ? 'text-amber-600 dark:text-amber-400 font-medium'
                                    : 'text-muted-foreground'
                                }`}
                              >
                                {holdHint}
                                {holdRemain != null && holdRemain > 0
                                  ? ` · 剩${fmtHours(holdRemain)}`
                                  : holdExtendHint || ''}
                                {p.hold_ai_extended ? ' · AI已延长' : ''}
                                {holdProgress != null ? ` (${holdProgress}%)` : ''}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-1 flex-wrap text-[9px]">
                            {healthScore != null && (
                              <span className={`${healthTone} font-medium`}>
                                健康{healthScore.toFixed(0)}{healthRegime ? ` · ${healthRegime}` : ''}
                              </span>
                            )}
                            {peakPct != null && peakPct > 0 && (
                              <span className="text-muted-foreground">
                                峰值+{peakPct.toFixed(1)}%
                              </span>
                            )}
                            {(stageCount > 0 || trailingActive) && (
                              <span className="text-blue-600 dark:text-blue-400">
                                TP{stageCount > 0 ? `已${stageCount}档` : ''}
                                {trailingActive ? ' · trailing中' : ''}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        <Badge
                          variant="outline"
                          className={p.side === 'long'
                            ? 'bg-green-50 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300'
                            : 'bg-red-50 text-red-700 border-red-300 dark:bg-red-950 dark:text-red-300'
                          }
                        >
                          {p.side === 'long' ? (
                            <><ArrowUpRight className="w-3 h-3 mr-0.5" />Long</>
                          ) : (
                            <><ArrowDownRight className="w-3 h-3 mr-0.5" />Short</>
                          )}
                        </Badge>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">{formatSize(p.size, baseSymbol)}</td>
                      <td className="py-2 pr-3 text-right font-mono">${formatPrice(p.entry_price, baseSymbol)}</td>
                      <td className="py-2 pr-3 text-right font-mono">${formatPrice(p.mark_price, baseSymbol)}</td>
                      <td className="py-2 pr-3 text-right">{p.leverage}x</td>
                      <td className="py-2 pr-3 text-right font-mono">${(p.margin ?? 0).toLocaleString()}</td>
                      <td className={`py-2 pr-3 text-right font-mono font-medium ${(p.unrealized_pnl ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {(p.unrealized_pnl ?? 0) >= 0 ? '+' : ''}{(p.unrealized_pnl ?? 0).toFixed(2)}
                      </td>
                      <td className={`py-2 pr-3 text-right font-mono font-medium ${(p.pnl_pct ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {(p.pnl_pct ?? 0) >= 0 ? '+' : ''}{(p.pnl_pct ?? 0).toFixed(2)}%
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-orange-600">
                        {(p.liquidation_price ?? 0) > 0 ? `$${formatPrice(p.liquidation_price, baseSymbol)}` : '-'}
                      </td>
                      <td className="py-2 pr-3 text-right text-[10px]">
                        {p.tp_price ? <span className="text-green-600">TP ${formatPrice(p.tp_price, baseSymbol)}</span> : ''}
                        {p.tp_price && p.sl_price ? ' / ' : ''}
                        {p.sl_price ? <span className="text-red-600">SL ${formatPrice(p.sl_price, baseSymbol)}</span> : ''}
                        {!p.tp_price && !p.sl_price ? '-' : ''}
                      </td>
                      <td className="py-2 text-right">
                        <div className="flex flex-col gap-1.5 items-end">
                          {/* 数量输入 + 百分比显示 */}
                          <div className="flex items-center gap-1.5">
                            <input
                              type="number"
                              step="any"
                              min={0}
                              max={p.size}
                              value={curQty}
                              onChange={e => handleQtyInput(Number(e.target.value))}
                              className="w-[90px] h-6 rounded border border-input bg-background px-1.5 text-[11px] font-mono text-right focus:outline-none focus:ring-1 focus:ring-ring"
                            />
                            <span className="text-[10px] text-muted-foreground w-[34px] text-right tabular-nums">
                              {curPct}%
                            </span>
                          </div>
                          {/* 快捷百分比按钮 */}
                          <div className="flex gap-0.5">
                            {SNAP_POINTS.map(pct => (
                              <button
                                key={pct}
                                onClick={() => handlePctChange(pct)}
                                className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-all ${
                                  curPct === pct
                                    ? 'bg-red-500 text-white shadow-sm'
                                    : 'bg-muted hover:bg-red-100 dark:hover:bg-red-950 text-muted-foreground hover:text-red-600'
                                }`}
                              >
                                {pct}%
                              </button>
                            ))}
                          </div>
                          {/* 平仓按钮 */}
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-6 text-[10px] text-red-600 border-red-200 hover:bg-red-50 w-full"
                            onClick={() => {
                              const qty = curPct >= 100 ? undefined : curQty;
                              handleClose(p.symbol, p.side, qty);
                            }}
                            disabled={closingId === p.id || curQty <= 0}
                          >
                            {closingId === p.id
                              ? <Loader2 className="w-3 h-3 animate-spin" />
                              : <XCircle className="w-3 h-3 mr-0.5" />}
                            {curPct >= 100 ? '全部平仓' : `平仓 ${curPct}%`}
                          </Button>
                        </div>
                      </td>
                    </tr>
                    );
                      });
                    });
                  })()}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 订单记录：成交记录 + 挂单信息（选项卡） */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <CardTitle className="text-sm flex items-center gap-2">
              <Activity className="w-4 h-4" />
              订单记录
            </CardTitle>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => setOrderSectionTab('records')}
                className={`px-2.5 py-1 rounded text-[10px] font-medium transition-colors ${
                  orderSectionTab === 'records'
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-muted'
                }`}
              >
                成交记录
              </button>
              <button
                type="button"
                onClick={() => setOrderSectionTab('pending')}
                className={`px-2.5 py-1 rounded text-[10px] font-medium transition-colors flex items-center gap-1 ${
                  orderSectionTab === 'pending'
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-muted'
                }`}
              >
                <Clock className="w-3 h-3" />
                挂单信息
                {pendingAttachedCount > 0 && (
                  <span className={`px-1.5 py-0.5 rounded-full text-[9px] ${
                    orderSectionTab === 'pending'
                      ? 'bg-primary-foreground/20 text-primary-foreground'
                      : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                  }`}>
                    {pendingAttachedCount}
                  </span>
                )}
              </button>
            </div>
          </div>
          {orderSectionTab === 'records' && (
            <div className="flex gap-1 mt-2">
              {(['filled', 'all'] as const).map(tab => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setRecordFilter(tab)}
                  className={`px-2 py-0.5 rounded text-[10px] transition-colors ${
                    recordFilter === tab
                      ? 'bg-muted text-foreground font-medium'
                      : 'text-muted-foreground hover:bg-muted/60'
                  }`}
                >
                  {{ filled: '仅已成交', all: '含已取消' }[tab]}
                </button>
              ))}
            </div>
          )}
        </CardHeader>
        <CardContent>
          {orderSectionTab === 'pending' ? (
            attachedOrders.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-6">
                暂无挂单（止盈/止损条件单会显示在这里）
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-muted-foreground">
                      <th className="text-left py-2 pr-3">时间</th>
                      <th className="text-left py-2 pr-3">币种</th>
                      <th className="text-left py-2 pr-3">挂单类型</th>
                      <th className="text-left py-2 pr-3">方向</th>
                      <th className="text-right py-2 pr-3">开仓价</th>
                      <th className="text-right py-2 pr-3">触发价</th>
                      <th className="text-right py-2 pr-3">数量</th>
                      <th className="text-right py-2 pr-3">杠杆</th>
                      <th className="text-right py-2">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {attachedOrders.map(o => {
                      const baseSymbol = (o.symbol || '').replace(/\/?USDT$/i, '') || o.symbol;
                      const triggerPrice = o.price ?? o.tp_price ?? o.sl_price ?? 0;
                      const entryPrice = o.entry_price;
                      const typeLabel = resolvePendingOrderLabel(o);
                      const typeTone = typeLabel.includes('止盈')
                        ? 'bg-green-50 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300'
                        : 'bg-red-50 text-red-700 border-red-300 dark:bg-red-950 dark:text-red-300';
                      const st = resolveAttachedOrderStatus(o);
                      return (
                        <tr key={o.id} className={`border-b last:border-0 hover:bg-muted/30 ${
                          o.status === 'pending' ? 'bg-amber-50/20 dark:bg-amber-950/10' : ''
                        }`}>
                          <td className="py-2 pr-3 text-muted-foreground">
                            <div>{o.created_at ? fmtShortDateTime(o.created_at) : '—'}</div>
                            {o.status === 'filled' && o.filled_at && (
                              <div className="text-[10px] text-green-600">触发 {fmtShortDateTime(o.filled_at)}</div>
                            )}
                          </td>
                          <td className="py-2 pr-3 font-medium">{o.symbol}</td>
                          <td className="py-2 pr-3">
                            <Badge variant="outline" className={`text-[10px] ${typeTone}`}>
                              {typeLabel}
                            </Badge>
                          </td>
                          <td className="py-2 pr-3">
                            <span className={o.side === 'buy' ? 'text-green-600' : 'text-red-600'}>
                              {o.side === 'buy' ? '买入' : '卖出'}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-muted-foreground">
                            {entryPrice != null && entryPrice > 0
                              ? `$${formatPrice(entryPrice, baseSymbol)}`
                              : '—'}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono font-medium">
                            {triggerPrice > 0 ? `$${formatPrice(triggerPrice, baseSymbol)}` : '—'}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono">{formatSize(o.quantity, baseSymbol)}</td>
                          <td className="py-2 pr-3 text-right">{o.leverage}x</td>
                          <td className="py-2 text-right">
                            <Badge variant="outline" className={`text-[10px] ${st.tone}`}>
                              {st.label}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          ) : historyRecords.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-6">暂无成交记录</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 pr-3">时间</th>
                    <th className="text-left py-2 pr-3">币种</th>
                    <th className="text-left py-2 pr-3">操作</th>
                    <th className="text-left py-2 pr-3">方向</th>
                    <th className="text-right py-2 pr-3">开仓价</th>
                    <th className="text-right py-2 pr-3">成交价</th>
                    <th className="text-right py-2 pr-3">数量</th>
                    <th className="text-right py-2 pr-3">杠杆</th>
                    <th className="text-right py-2 pr-3">手续费</th>
                    <th className="text-right py-2 pr-3">盈亏</th>
                    <th className="text-right py-2">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {historyRecords
                    .map(o => {
                      const baseSymbol = (o.symbol || '').replace(/\/?USDT$/i, '') || o.symbol;
                      const isClose = !!o.close_reason;
                      const fillPrice = o.filled_price ?? o.price ?? 0;
                      const entryPrice = o.entry_price ?? (isClose ? null : fillPrice);
                      const pnl = o.pnl ?? 0;
                      const fromAttached = isAttachedOrder(o) && o.status === 'filled';
                      const recordSt = resolveRecordStatus(o);
                      return (
                    <tr key={o.id} className={`border-b last:border-0 hover:bg-muted/30 ${isClose ? 'bg-muted/20' : ''}`}>
                      <td className="py-2 pr-3 text-muted-foreground">
                        {o.filled_at ? fmtShortDateTime(o.filled_at)
                          : o.created_at ? fmtShortDateTime(o.created_at)
                          : '-'}
                      </td>
                      <td className="py-2 pr-3 font-medium">{o.symbol}</td>
                      <td className="py-2 pr-3">
                        {isClose ? (
                          <Badge variant="outline" className={`text-[10px] ${getCloseReasonColorClass(o.close_reason!, pnl)}`}>
                            {getCloseReasonLabel(o.close_reason!, pnl)}
                            {fromAttached ? ' ·挂单' : ''}
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-[10px] bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300">
                            开仓
                          </Badge>
                        )}
                      </td>
                      <td className="py-2 pr-3">
                        <span className={o.side === 'buy' ? 'text-green-600' : 'text-red-600'}>
                          {o.side === 'buy' ? '买入' : '卖出'}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-muted-foreground">
                        {entryPrice != null && entryPrice > 0
                          ? `$${formatPrice(entryPrice, baseSymbol)}`
                          : '—'}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">
                        {fillPrice > 0 ? `$${formatPrice(fillPrice, baseSymbol)}` : '—'}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono">{formatSize(o.filled_quantity || o.quantity, baseSymbol)}</td>
                      <td className="py-2 pr-3 text-right">{o.leverage}x</td>
                      <td className="py-2 pr-3 text-right font-mono text-muted-foreground">{(o.fee ?? 0).toFixed(4)}</td>
                      <td className={`py-2 pr-3 text-right font-mono font-semibold ${
                        o.pnl === null || o.pnl === undefined ? 'text-muted-foreground' : o.pnl >= 0 ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {o.pnl !== null && o.pnl !== undefined
                          ? `${o.pnl >= 0 ? '+' : ''}${o.pnl.toFixed(2)}`
                          : '-'}
                      </td>
                      <td className="py-2 text-right">
                        <Badge variant="outline" className={`text-[10px] ${recordSt.tone}`}>
                          {recordSt.label}
                        </Badge>
                      </td>
                    </tr>
                  );})}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
    </div>
  );
}

function isAttachedOrder(o: PaperOrder): boolean {
  if (o.order_type === 'take_profit' || o.order_type === 'stop_loss') return true;
  return o.status === 'pending' && !!o.close_reason;
}

function resolveAttachedOrderStatus(o: PaperOrder): { label: string; tone: string } {
  if (o.status === 'pending') {
    return { label: '待触发', tone: 'text-amber-600 border-amber-300' };
  }
  if (o.status === 'filled') {
    return { label: '已触发成交', tone: 'text-green-600 border-green-300' };
  }
  if (o.status === 'rejected') {
    return { label: '已拒单', tone: 'text-red-600 border-red-300' };
  }
  return { label: '已取消', tone: 'text-gray-500 border-gray-300' };
}

function resolveRecordStatus(o: PaperOrder): { label: string; tone: string } {
  if (o.status === 'filled' && isAttachedOrder(o)) {
    return { label: '挂单已触发', tone: 'text-green-600 border-green-300' };
  }
  if (o.status === 'filled') {
    return { label: '已成交', tone: 'text-green-600 border-green-300' };
  }
  if (o.status === 'rejected') {
    return { label: '已拒单', tone: 'text-red-600 border-red-300' };
  }
  return { label: '已取消', tone: 'text-gray-500 border-gray-300' };
}

function resolvePendingOrderLabel(o: PaperOrder): string {
  const cr = o.close_reason || '';
  const entry = o.entry_price ?? 0;
  const trigger = o.price ?? o.sl_price ?? o.tp_price ?? 0;

  if (o.order_type === 'take_profit' || cr === 'tp' || cr === 'breakeven_tp' || cr === 'safety_tp') {
    return '止盈挂单';
  }
  if (cr === 'breakeven_sl') {
    return '保本止损挂单';
  }
  if (o.order_type === 'stop_loss' || cr === 'sl') {
    if (entry > 0 && trigger > 0) {
      if (o.side === 'sell' && trigger >= entry) return '保本止损挂单';
      if (o.side === 'buy' && trigger <= entry) return '保本止损挂单';
    }
    return '止损挂单';
  }
  if (cr) {
    return '条件平仓挂单';
  }
  return '限价挂单';
}

// ── StatCard 组件 ──

function StatCard({
  label,
  value,
  icon,
  change,
  color,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  change?: number;
  color: string;
}) {
  const colorMap: Record<string, string> = {
    blue: 'from-blue-500 to-blue-600',
    green: 'from-green-500 to-green-600',
    red: 'from-red-500 to-red-600',
    amber: 'from-amber-500 to-amber-600',
  };

  return (
    <Card className="relative overflow-hidden">
      <CardContent className="p-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-muted-foreground">{label}</span>
          <div className={`w-6 h-6 rounded-md bg-gradient-to-br ${colorMap[color] || colorMap.blue} flex items-center justify-center text-white`}>
            {icon}
          </div>
        </div>
        <p className="text-lg font-bold tracking-tight">{value}</p>
        {change !== undefined && (
          <p className={`text-[10px] font-medium ${change >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {change >= 0 ? '+' : ''}{change.toFixed(2)}%
          </p>
        )}
      </CardContent>
    </Card>
  );
}
// test hot reload 1775266502
