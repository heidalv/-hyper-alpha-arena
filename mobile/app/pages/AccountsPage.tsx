import React, { useState, useEffect, useCallback } from 'react'
import TouchButton from '@/components/ui/TouchButton'
import BottomSheet from '@/components/ui/BottomSheet'
import { listAccounts, createAccount, deleteAccount, type AccountListItem } from '@/api/accounts'
import { apiRequest } from '@/api/client'

interface PageProps {
  ws?: any
}

export default function AccountsPage({ ws }: PageProps) {
  const [accounts, setAccounts] = useState<AccountListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreatePaper, setShowCreatePaper] = useState(false)
  const [showCreateAI, setShowCreateAI] = useState(false)
  const [acting, setActing] = useState(false)
  const [selectedAccount, setSelectedAccount] = useState<AccountListItem | null>(null)
  const [viewMode, setViewMode] = useState<'list' | 'detail'>('list')

  // Detail view state for paper accounts
  const [detailBalance, setDetailBalance] = useState<any>(null)
  const [detailPositions, setDetailPositions] = useState<any[]>([])
  const [detailLoading, setDetailLoading] = useState(false)

  // Create form states
  const [paperName, setPaperName] = useState('')
  const [paperCapital, setPaperCapital] = useState('100000')
  const [aiName, setAiName] = useState('')
  const [aiModel, setAiModel] = useState('deepseek-chat')
  const [aiPersonality, setAiPersonality] = useState('moderate')

  const loadAccounts = useCallback(async () => {
    try {
      const data = await listAccounts()
      setAccounts(data || [])
    } catch (e) {
      console.error('[Accounts] load failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAccounts() }, [loadAccounts])

  const paperAccounts = accounts.filter(a => a.trading_mode === 'paper' || a.account_type === 'PAPER')
  const aiAccounts = accounts.filter(a => a.account_type === 'AI' || a.account_type?.toUpperCase() === 'AI')

  // ── Handlers ──
  const handleCreatePaper = async () => {
    if (!paperName.trim()) { alert('请输入账户名称'); return }
    setActing(true)
    try {
      await createAccount({ name: paperName.trim(), account_type: 'PAPER', trading_mode: 'paper', initial_capital: parseFloat(paperCapital) || 100000 })
      setShowCreatePaper(false)
      setPaperName(''); setPaperCapital('100000')
      await loadAccounts()
    } catch (e: any) { alert(e.message || '创建失败') }
    finally { setActing(false) }
  }

  const handleCreateAI = async () => {
    if (!aiName.trim()) { alert('请输入交易员名称'); return }
    setActing(true)
    try {
      await createAccount({ name: aiName.trim(), account_type: 'AI', trading_mode: 'live', model: aiModel, auto_trading_enabled: false })
      setShowCreateAI(false)
      setAiName(''); setAiModel('deepseek-chat')
      await loadAccounts()
    } catch (e: any) { alert(e.message || '创建失败') }
    finally { setActing(false) }
  }

  const handleDelete = async (account: AccountListItem) => {
    if (!confirm(`确定删除账户「${account.name}」？此操作不可撤销。`)) return
    setActing(true)
    try {
      await deleteAccount(account.id)
      await loadAccounts()
    } catch (e: any) { alert(e.message || '删除失败') }
    finally { setActing(false) }
  }

  const handleSelectAccount = async (account: AccountListItem) => {
    setSelectedAccount(account)
    setViewMode('detail')
    const isPaper = account.trading_mode === 'paper' || account.account_type === 'PAPER'
    if (isPaper) {
      setDetailLoading(true)
      setDetailBalance(null)
      setDetailPositions([])
      try {
        const [balance, positions] = await Promise.all([
          apiRequest<any>(`/paper/balance/${account.id}`).catch(() => null),
          apiRequest<any[]>(`/paper/positions/${account.id}`).catch(() => []),
        ])
        setDetailBalance(balance)
        setDetailPositions(positions || [])
      } catch (e) {
        console.error('[Accounts] fetch paper detail:', e)
      } finally {
        setDetailLoading(false)
      }
    }
  }

  // ── Render: Detail View ──
  if (viewMode === 'detail' && selectedAccount) {
    const isPaper = selectedAccount.trading_mode === 'paper' || selectedAccount.account_type === 'PAPER'
    return (
      <div className="p-4 space-y-4">
        {/* Back */}
        <button onClick={() => { setViewMode('list'); setSelectedAccount(null) }} className="flex items-center gap-2 text-sm text-terminal-muted hover:text-terminal-text active:opacity-70">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6" /></svg>
          返回账户列表
        </button>

        {/* Header */}
        <div className="card text-center space-y-2">
          <div className="text-3xl">{isPaper ? '🛡' : '🤖'}</div>
          <h2 className="text-xl font-bold">{selectedAccount.name}</h2>
          <div className="flex justify-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded bg-terminal-primary/20 text-terminal-primary">{selectedAccount.account_type}</span>
            <span className="text-xs px-2 py-0.5 rounded bg-terminal-card text-terminal-muted">{selectedAccount.trading_mode}</span>
          </div>
        </div>

        {/* Details */}
        <div className="card grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-xs text-terminal-muted">账户ID</p>
            <p className="font-mono">#{selectedAccount.id}</p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">初始资金</p>
            <p className="font-mono">${(selectedAccount.initial_capital || 0).toLocaleString()}</p>
          </div>
          {isPaper && detailBalance && (
            <>
              <div>
                <p className="text-xs text-terminal-muted">当前余额</p>
                <p className="font-mono font-semibold">
                  ${((detailBalance.cash || detailBalance.balance || detailBalance.equity || 0)).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                </p>
              </div>
              <div>
                <p className="text-xs text-terminal-muted">冻结保证金</p>
                <p className="font-mono">${((detailBalance.frozen_cash || detailBalance.margin || 0)).toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
              </div>
              <div>
                <p className="text-xs text-terminal-muted">持仓盈亏</p>
                <p className={`font-mono font-semibold ${(detailBalance.unrealized_pnl || 0) >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                  {(detailBalance.unrealized_pnl || 0) >= 0 ? '+' : '-'}${Math.abs(detailBalance.unrealized_pnl || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                </p>
              </div>
              <div>
                <p className="text-xs text-terminal-muted">已实现盈亏</p>
                <p className={`font-mono ${(detailBalance.realized_pnl || 0) >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                  {(detailBalance.realized_pnl || 0) >= 0 ? '+' : '-'}${Math.abs(detailBalance.realized_pnl || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                </p>
              </div>
            </>
          )}
          {isPaper && detailLoading && (
            <div className="col-span-2 text-center text-terminal-muted text-xs py-2">加载账户数据...</div>
          )}
          {!isPaper && (
            <>
              <div>
                <p className="text-xs text-terminal-muted">模型</p>
                <p className="font-mono text-xs">{selectedAccount.model || '-'}</p>
              </div>
              <div>
                <p className="text-xs text-terminal-muted">自动交易</p>
                <p className={selectedAccount.auto_trading_enabled === 'true' ? 'text-terminal-profit' : 'text-terminal-muted'}>
                  {selectedAccount.auto_trading_enabled === 'true' ? '已启用' : '已关闭'}
                </p>
              </div>
            </>
          )}
        </div>

        {/* Paper Account: Positions */}
        {isPaper && !detailLoading && detailPositions.length > 0 && (
          <div className="card">
            <p className="text-xs text-terminal-muted mb-2 font-semibold">▸ 当前持仓 ({detailPositions.length})</p>
            <div className="space-y-1.5">
              {detailPositions.filter((p: any) => p.status === 'open').map((p: any, i: number) => {
                const isProfit = (p.unrealized_pnl || 0) >= 0
                return (
                  <div key={p.id || i} className={`flex items-center justify-between py-1.5 px-2 rounded border-l-4 ${isProfit ? 'border-l-terminal-profit' : 'border-l-terminal-loss'} bg-terminal-bg/50`}>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm">{p.symbol}</span>
                      <span className={`text-[10px] px-1 py-0.5 rounded ${p.side === 'long' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'}`}>
                        {p.side === 'long' ? 'Long' : 'Short'} {p.leverage}x
                      </span>
                    </div>
                    <div className="text-right text-xs">
                      <p className={`font-mono font-semibold ${isProfit ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                        {isProfit ? '+' : '-'}${Math.abs(p.unrealized_pnl || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                      </p>
                      <p className="text-terminal-muted text-[10px]">{p.size} 张</p>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
        {isPaper && !detailLoading && detailBalance && detailPositions.filter((p: any) => p.status === 'open').length === 0 && (
          <div className="card text-center text-terminal-muted py-4 text-sm">暂无活跃持仓</div>
        )}

        {/* Actions */}
        <TouchButton variant="danger" fullWidth onClick={() => handleDelete(selectedAccount)} loading={acting}>
          删除此账户
        </TouchButton>
      </div>
    )
  }

  // ── Render: List View ──
  return (
    <div className="p-4 space-y-4">
      {/* Section: Paper Accounts */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-terminal-muted uppercase tracking-wide">模拟账户</h3>
          <button onClick={() => setShowCreatePaper(true)} className="flex items-center gap-1 text-xs text-terminal-primary active:opacity-70">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
            新建
          </button>
        </div>
        {loading ? (
          <div className="card text-center text-terminal-muted py-8">加载中...</div>
        ) : paperAccounts.length === 0 ? (
          <div className="card text-center text-terminal-muted py-6">
            <p className="text-sm">暂无模拟账户</p>
            <p className="text-xs mt-1">点击「新建」创建模拟账户</p>
          </div>
        ) : (
          <div className="space-y-2">
            {paperAccounts.map(a => (
              <div key={a.id} onClick={() => handleSelectAccount(a)} className="card flex items-center justify-between cursor-pointer active:opacity-80 border-l-4 border-l-terminal-primary">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">🛡</span>
                  <div>
                    <p className="font-semibold text-sm">{a.name}</p>
                    <p className="text-xs text-terminal-muted">PAPER · ID #{a.id}</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-mono">${(a.current_cash || a.initial_capital || 0).toLocaleString()}</p>
                  <button onClick={(e) => { e.stopPropagation(); handleDelete(a) }} className="text-xs text-terminal-loss mt-1 active:opacity-70">删除</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Section: AI Traders */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-terminal-muted uppercase tracking-wide">AI 交易员</h3>
          <button onClick={() => setShowCreateAI(true)} className="flex items-center gap-1 text-xs text-terminal-primary active:opacity-70">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
            新建
          </button>
        </div>
        {loading ? (
          <div className="card text-center text-terminal-muted py-8">加载中...</div>
        ) : aiAccounts.length === 0 ? (
          <div className="card text-center text-terminal-muted py-6">
            <p className="text-sm">暂无 AI 交易员</p>
            <p className="text-xs mt-1">点击「新建」创建 AI 交易员</p>
          </div>
        ) : (
          <div className="space-y-2">
            {aiAccounts.map(a => (
              <div key={a.id} onClick={() => handleSelectAccount(a)} className="card flex items-center justify-between cursor-pointer active:opacity-80 border-l-4 border-l-purple-500">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">🤖</span>
                  <div>
                    <p className="font-semibold text-sm">{a.name}</p>
                    <p className="text-xs text-terminal-muted">{a.model || 'AI'} · {a.auto_trading_enabled === 'true' ? '● 运行中' : '○ 待启动'}</p>
                  </div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); handleDelete(a) }} className="text-xs text-terminal-loss active:opacity-70">删除</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Create Paper Account BottomSheet ── */}
      <BottomSheet open={showCreatePaper} onClose={() => setShowCreatePaper(false)} title="新建模拟账户">
        <div className="space-y-4">
          <div>
            <label className="text-xs text-terminal-muted block mb-1">账户名称</label>
            <input
              type="text"
              value={paperName}
              onChange={(e) => setPaperName(e.target.value)}
              placeholder="例如: 主力模拟账户"
              className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-primary"
            />
          </div>
          <div>
            <label className="text-xs text-terminal-muted block mb-1">初始资金 (USDT)</label>
            <input
              type="number"
              value={paperCapital}
              onChange={(e) => setPaperCapital(e.target.value)}
              placeholder="100000"
              className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-primary"
            />
          </div>
          <div className="flex gap-3">
            <TouchButton variant="ghost" fullWidth onClick={() => setShowCreatePaper(false)}>取消</TouchButton>
            <TouchButton variant="primary" fullWidth loading={acting} onClick={handleCreatePaper}>创建</TouchButton>
          </div>
        </div>
      </BottomSheet>

      {/* ── Create AI Trader BottomSheet ── */}
      <BottomSheet open={showCreateAI} onClose={() => setShowCreateAI(false)} title="新建 AI 交易员">
        <div className="space-y-4">
          <div>
            <label className="text-xs text-terminal-muted block mb-1">交易员名称</label>
            <input
              type="text"
              value={aiName}
              onChange={(e) => setAiName(e.target.value)}
              placeholder="例如: 主力 AI 交易员"
              className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text placeholder-terminal-muted/50 focus:outline-none focus:border-terminal-primary"
            />
          </div>
          <div>
            <label className="text-xs text-terminal-muted block mb-1">LLM 模型</label>
            <select
              value={aiModel}
              onChange={(e) => setAiModel(e.target.value)}
              className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text focus:outline-none focus:border-terminal-primary"
            >
              <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
              <option value="deepseek-chat">DeepSeek Chat</option>
              <option value="gpt-4-turbo">GPT-4 Turbo</option>
              <option value="gpt-4o">GPT-4o</option>
              <option value="claude-3-opus">Claude 3 Opus</option>
              <option value="qwen-max">Qwen Max</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-terminal-muted block mb-1">性格预设</label>
            <div className="flex gap-2">
              {(['conservative', 'moderate', 'aggressive'] as const).map(p => (
                <button
                  key={p}
                  onClick={() => setAiPersonality(p)}
                  className={`flex-1 py-2 rounded text-sm font-medium border transition-colors ${
                    aiPersonality === p
                      ? 'bg-terminal-primary/20 border-terminal-primary text-terminal-primary'
                      : 'bg-terminal-bg border-terminal-border text-terminal-muted'
                  }`}
                >
                  {{ conservative: '保守', moderate: '中性', aggressive: '激进' }[p]}
                </button>
              ))}
            </div>
          </div>
          <div className="flex gap-3">
            <TouchButton variant="ghost" fullWidth onClick={() => setShowCreateAI(false)}>取消</TouchButton>
            <TouchButton variant="primary" fullWidth loading={acting} onClick={handleCreateAI}>创建</TouchButton>
          </div>
        </div>
      </BottomSheet>
    </div>
  )
}
