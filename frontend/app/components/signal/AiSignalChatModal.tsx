import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import { toast } from 'react-hot-toast'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import PacmanLoader from '@/components/ui/pacman-loader'
import { TradingAccount } from '@/lib/api'

interface SignalConfig {
  name: string
  symbol: string
  description?: string
  _type?: 'signal' | 'pool'  // Type identifier from backend
  // For single signal
  trigger_condition?: {
    metric: string
    operator?: string
    threshold?: number
    time_window?: string
    direction?: string
    ratio_threshold?: number
    volume_threshold?: number
  }
  // For signal pool
  logic?: 'AND' | 'OR'
  signals?: Array<{
    metric?: string      // frontend field name
    indicator?: string   // AI output field name (same as metric)
    operator?: string
    threshold?: number
    time_window?: string
    // taker_volume composite signal fields
    direction?: string
    ratio_threshold?: number
    volume_threshold?: number
  }>
}

interface AnalysisEntry {
  type: 'reasoning' | 'tool_call' | 'tool_result'
  round?: number
  content?: string
  name?: string
  arguments?: Record<string, unknown>
  result?: Record<string, unknown>
}

interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  signal_configs?: SignalConfig[] | null
  isStreaming?: boolean
  statusText?: string
  analysisLog?: AnalysisEntry[]
}

interface Conversation {
  id: number
  title: string
  created_at: string
  updated_at: string
}

interface AiSignalChatModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreateSignal: (config: SignalConfig) => Promise<boolean>  // Returns true on success
  onCreatePool: (config: SignalConfig) => Promise<boolean>    // Create signal pool
  onPreviewSignal: (config: SignalConfig) => void
  accounts: TradingAccount[]
  accountsLoading: boolean
}

// Component implementation continues below
export default function AiSignalChatModal({
  open,
  onOpenChange,
  onCreateSignal,
  onCreatePool,
  onPreviewSignal,
  accounts,
  accountsLoading,
}: AiSignalChatModalProps) {
  const { t } = useTranslation()
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loadingConversations, setLoadingConversations] = useState(false)
  const [currentConversationId, setCurrentConversationId] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [userInput, setUserInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [allSignalConfigs, setAllSignalConfigs] = useState<SignalConfig[]>([])

  // Filter AI accounts
  const aiAccounts = accounts.filter(acc => acc.account_type === 'AI')

  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  // Load conversations when modal opens and select default AI account
  useEffect(() => {
    if (open) {
      loadConversations()
      // Select first AI account by default
      if (aiAccounts.length > 0 && !selectedAccountId) {
        setSelectedAccountId(aiAccounts[0].id)
      }
    }
  }, [open, accounts])

  // Load messages when conversation changes
  useEffect(() => {
    if (currentConversationId) {
      loadMessages(currentConversationId)
    }
  }, [currentConversationId])

  const loadConversations = async () => {
    setLoadingConversations(true)
    try {
      const response = await fetch('/api/signals/ai-conversations')
      if (response.ok) {
        const data = await response.json()
        setConversations(data.conversations || [])
      }
    } catch (error) {
      console.error('Failed to load conversations:', error)
    } finally {
      setLoadingConversations(false)
    }
  }

  const loadMessages = async (conversationId: number) => {
    try {
      const response = await fetch(`/api/signals/ai-conversations/${conversationId}/messages`)
      if (response.ok) {
        const data = await response.json()
        setMessages(data.messages || [])
        // Collect all signal configs from messages
        const configs: SignalConfig[] = []
        data.messages.forEach((m: Message) => {
          if (m.role === 'assistant' && m.signal_configs) {
            configs.push(...m.signal_configs)
          }
        })
        setAllSignalConfigs(configs)
      }
    } catch (error) {
      console.error('Failed to load messages:', error)
    }
  }

  const sendMessage = async () => {
    if (!userInput.trim() || !selectedAccountId) return
    const userMessage = userInput.trim()
    setUserInput('')
    setLoading(true)

    const tempUserMsgId = Date.now()
    const tempAssistantMsgId = tempUserMsgId + 1
    const tempUserMsg: Message = { id: tempUserMsgId, role: 'user', content: userMessage }
    const tempAssistantMsg: Message = {
      id: tempAssistantMsgId,
      role: 'assistant',
      content: '',
      isStreaming: true,
      statusText: 'Connecting...',
    }
    setMessages(prev => [...prev, tempUserMsg, tempAssistantMsg])

    try {
      const response = await fetch('/api/signals/ai-chat-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          accountId: selectedAccountId,
          userMessage: userMessage,
          conversationId: currentConversationId,
        }),
      })

      if (!response.ok) throw new Error('Failed to send message')
      if (!response.body) throw new Error('No response body')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let finalContent = ''
      let finalSignalConfigs: SignalConfig[] = []
      let finalConversationId: number | null = null
      let finalMessageId: number | null = null

      let currentEventType = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEventType = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              handleSSEEvent(currentEventType, data, tempAssistantMsgId, (updates) => {
                if (updates.content !== undefined) finalContent = updates.content
                if (updates.signalConfigs) finalSignalConfigs = updates.signalConfigs
                if (updates.conversationId) finalConversationId = updates.conversationId
                if (updates.messageId) finalMessageId = updates.messageId
              })
            } catch {}
            currentEventType = ''
          }
        }
      }

      // Finalize the message
      setMessages(prev => prev.map(m =>
        m.id === tempAssistantMsgId
          ? { ...m, content: finalContent, signal_configs: finalSignalConfigs, isStreaming: false, statusText: undefined }
          : m
      ))
      if (finalSignalConfigs.length > 0) {
        setAllSignalConfigs(prev => [...prev, ...finalSignalConfigs])
      }
      if (!currentConversationId && finalConversationId) {
        setCurrentConversationId(finalConversationId)
        loadConversations()
      }
    } catch (error) {
      console.error('Error sending message:', error)
      toast.error('Failed to send message')
      setMessages(prev => prev.filter(m => m.id !== tempUserMsgId && m.id !== tempAssistantMsgId))
    } finally {
      setLoading(false)
    }
  }

  const handleSSEEvent = (
    eventType: string,
    data: Record<string, unknown>,
    msgId: number,
    onUpdate: (updates: { content?: string; signalConfigs?: SignalConfig[]; conversationId?: number; messageId?: number }) => void
  ) => {
    if (eventType === 'status') {
      setMessages(prev => prev.map(m =>
        m.id === msgId ? { ...m, statusText: data.message as string } : m
      ))
    } else if (eventType === 'reasoning') {
      const reasoning = data.content as string || ''
      const entry: AnalysisEntry = { type: 'reasoning', content: reasoning }
      setMessages(prev => prev.map(m =>
        m.id === msgId ? {
          ...m,
          statusText: `Thinking: ${reasoning.slice(0, 80)}...`,
          analysisLog: [...(m.analysisLog || []), entry]
        } : m
      ))
    } else if (eventType === 'content') {
      const content = data.content as string
      onUpdate({ content })
      setMessages(prev => prev.map(m =>
        m.id === msgId ? { ...m, content, statusText: undefined } : m
      ))
    } else if (eventType === 'signal_config') {
      const config = data.config as SignalConfig
      if (config) {
        onUpdate({ signalConfigs: [config] })
      }
    } else if (eventType === 'done') {
      const content = data.content as string
      const signalConfigs = data.signal_configs as SignalConfig[]
      onUpdate({
        conversationId: data.conversation_id as number,
        messageId: data.message_id as number,
        content: content,
        signalConfigs: signalConfigs,
      })
    } else if (eventType === 'error') {
      toast.error(data.message as string || 'AI generation failed')
    } else if (eventType === 'tool_call') {
      const entry: AnalysisEntry = {
        type: 'tool_call',
        name: data.name as string,
        arguments: data.arguments as Record<string, unknown>
      }
      setMessages(prev => prev.map(m =>
        m.id === msgId ? {
          ...m,
          statusText: `Calling ${data.name}...`,
          analysisLog: [...(m.analysisLog || []), entry]
        } : m
      ))
    } else if (eventType === 'tool_result') {
      const entry: AnalysisEntry = {
        type: 'tool_result',
        name: data.name as string,
        result: data.result as Record<string, unknown>
      }
      setMessages(prev => prev.map(m =>
        m.id === msgId ? {
          ...m,
          statusText: `Got result from ${data.name}`,
          analysisLog: [...(m.analysisLog || []), entry]
        } : m
      ))
    }
  }

  const startNewConversation = () => {
    setCurrentConversationId(null)
    setMessages([])
    setAllSignalConfigs([])
  }

  const getMetricLabel = (metric: string) => {
    const labels: Record<string, string> = {
      oi: 'Open Interest',
      oi_delta_percent: 'OI Delta %',
      cvd: 'CVD',
      funding_rate: 'Funding Rate',
      depth_ratio: 'Depth Ratio',
      order_imbalance: 'Order Imbalance',
      taker_buy_ratio: 'Taker Buy Ratio',
      taker_volume: 'Taker Volume',
    }
    return labels[metric] || metric
  }

  const getOperatorLabel = (op: string) => {
    const labels: Record<string, string> = {
      greater_than: '>',
      less_than: '<',
      greater_than_or_equal: '>=',
      less_than_or_equal: '<=',
      abs_greater_than: 'abs >',
    }
    return labels[op] || op
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="w-[95vw] max-w-[1400px] h-[90vh] max-h-[90vh] flex flex-col p-0 overflow-hidden"
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader className="px-6 py-4 border-b shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <DialogTitle>{t('signals.aiGenerator.title', 'AI Signal Generator')}</DialogTitle>
              <span className="text-xs text-muted-foreground">
                {t('signals.aiGenerator.subtitle', '(Requires Function Call support to invoke analysis tools. Reasoning models work best.)')}
              </span>
            </div>
            {(loadingConversations || accountsLoading) && <PacmanLoader className="w-8 h-4" />}
          </div>
          <div className="flex items-center gap-4 mt-4">
            <div className="flex-1">
              <label className="text-xs text-muted-foreground mb-1 block">{t('signals.aiGenerator.aiTrader', 'AI Trader')}</label>
              <Select
                value={selectedAccountId?.toString()}
                onValueChange={(val) => setSelectedAccountId(parseInt(val))}
                disabled={accountsLoading}
              >
                <SelectTrigger>
                  <SelectValue placeholder={accountsLoading ? t('signals.aiGenerator.loading', 'Loading...') : t('signals.aiGenerator.selectAiTrader', 'Select AI Trader')} />
                </SelectTrigger>
                <SelectContent>
                  {aiAccounts.map(acc => (
                    <SelectItem key={acc.id} value={acc.id.toString()}>
                      {acc.name} ({acc.model})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex-1">
              <label className="text-xs text-muted-foreground mb-1 block">{t('signals.aiGenerator.conversation', 'Conversation')}</label>
              <div className="flex gap-2">
                <Select
                  value={currentConversationId?.toString() || 'new'}
                  onValueChange={(val) => {
                    if (val === 'new') startNewConversation()
                    else setCurrentConversationId(parseInt(val))
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('signals.aiGenerator.newConversation', 'New Conversation')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="new">{t('signals.aiGenerator.newConversation', 'New Conversation')}</SelectItem>
                    {conversations.map(conv => (
                      <SelectItem key={conv.id} value={conv.id.toString()}>
                        {conv.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button variant="outline" size="sm" onClick={startNewConversation}>New</Button>
              </div>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* Left: Chat Area (45%) */}
          <ChatArea
            messages={messages}
            userInput={userInput}
            setUserInput={setUserInput}
            loading={loading}
            sendMessage={sendMessage}
            messagesEndRef={messagesEndRef}
            hasAccount={!!selectedAccountId}
            t={t}
          />

          {/* Right: Signal Cards (55%) */}
          <SignalCardsPanel
            configs={allSignalConfigs}
            onPreview={onPreviewSignal}
            onCreate={onCreateSignal}
            onCreatePool={onCreatePool}
            getMetricLabel={getMetricLabel}
            getOperatorLabel={getOperatorLabel}
            t={t}
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}

// Chat Area Component
function ChatArea({
  messages, userInput, setUserInput, loading, sendMessage, messagesEndRef, hasAccount, t
}: {
  messages: Message[]
  userInput: string
  setUserInput: (v: string) => void
  loading: boolean
  sendMessage: () => void
  messagesEndRef: React.RefObject<HTMLDivElement>
  hasAccount: boolean
  t: (key: string, fallback?: string) => string
}) {
  return (
    <div className="w-[45%] flex flex-col border-r min-h-0">
      <div className="flex-1 overflow-y-auto p-4">
        <div className="space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-muted-foreground py-8">
              <p className="text-sm">{t('signals.aiGenerator.describeSignal', 'Describe the signal you want to create')}</p>
              <p className="text-xs mt-2">{t('signals.aiGenerator.example', 'Example: "Create a signal for BTC when OI increases by 1%"')}</p>
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-lg p-3 ${
                msg.role === 'user' ? 'bg-primary text-white' : 'bg-muted'
              }`}>
                <div className={`text-xs font-semibold mb-1 ${msg.role === 'user' ? 'text-white/70' : 'opacity-70'}`}>
                  {msg.role === 'user' ? t('signals.aiGenerator.you', 'You') : t('signals.aiGenerator.aiAssistant', 'AI Assistant')}
                  {msg.isStreaming && msg.statusText && (
                    <span className="ml-2 text-primary animate-pulse">({msg.statusText})</span>
                  )}
                </div>
                {/* Show analysis log during streaming */}
                {msg.isStreaming && msg.analysisLog && msg.analysisLog.length > 0 && (
                  <div className="mb-2 text-xs bg-background/50 rounded p-2 max-h-32 overflow-y-auto">
                    {msg.analysisLog.slice(-5).map((entry, idx) => (
                      <div key={idx} className="mb-1 last:mb-0">
                        {entry.type === 'tool_call' && (
                          <span className="text-blue-500">→ {entry.name}({Object.entries(entry.arguments || {}).map(([k,v]) => `${k}=${v}`).join(', ')})</span>
                        )}
                        {entry.type === 'tool_result' && (
                          <span className="text-green-500">← {entry.name}: {JSON.stringify(entry.result).slice(0, 80)}...</span>
                        )}
                        {entry.type === 'reasoning' && (
                          <span className="text-gray-500 italic">{(entry.content || '').slice(0, 100)}...</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <div className={`text-sm prose prose-sm max-w-none ${
                  msg.role === 'user' ? 'prose-invert text-white' : 'dark:prose-invert'
                } [&_details]:bg-muted/50 [&_details]:rounded-lg [&_details]:p-2 [&_details]:mb-3 [&_details]:text-xs [&_summary]:cursor-pointer [&_summary]:font-medium [&_summary]:text-muted-foreground [&_details>div]:mt-2 [&_details>div]:max-h-64 [&_details>div]:overflow-y-auto [&_details>div]:whitespace-pre-wrap [&_details>div]:text-muted-foreground`}>
                  {msg.content ? (
                    <ReactMarkdown rehypePlugins={[rehypeRaw]}>{msg.content}</ReactMarkdown>
                  ) : msg.isStreaming ? (
                    <span className="text-muted-foreground italic">{t('signals.aiGenerator.generating', 'Generating...')}</span>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>
      <div className="shrink-0 p-4 border-t">
        <div className="flex gap-2 items-end">
          <textarea
            placeholder={t('signals.aiGenerator.inputPlaceholder', 'Describe your signal...')}
            value={userInput}
            onChange={(e) => setUserInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                sendMessage()
              }
            }}
            disabled={loading || !hasAccount}
            className="flex-1 min-h-[80px] rounded-md border px-3 py-2 text-sm resize-y"
            rows={3}
          />
          <Button onClick={sendMessage} disabled={loading || !userInput.trim() || !hasAccount} className="h-[80px]">
            {loading ? t('signals.aiGenerator.sending', 'Sending...') : t('signals.aiGenerator.send', 'Send')}
          </Button>
        </div>
      </div>
    </div>
  )
}

// Signal Cards Panel Component
function SignalCardsPanel({
  configs, onPreview, onCreate, onCreatePool, getMetricLabel, getOperatorLabel, t
}: {
  configs: SignalConfig[]
  onPreview: (config: SignalConfig) => void
  onCreate: (config: SignalConfig) => Promise<boolean>
  onCreatePool: (config: SignalConfig) => Promise<boolean>
  getMetricLabel: (m: string) => string
  getOperatorLabel: (o: string) => string
  t: (key: string, fallback?: string) => string
}) {
  // Track which signals/pools are being created and which have been created
  const [creatingSignals, setCreatingSignals] = useState<Set<string>>(new Set())
  const [createdSignals, setCreatedSignals] = useState<Set<string>>(new Set())

  const handleCreate = async (config: SignalConfig, isPool: boolean) => {
    const signalKey = config.name || `signal-${configs.indexOf(config)}`
    setCreatingSignals(prev => new Set(prev).add(signalKey))
    
    // Clean and normalize config before saving
    const cleanConfig = JSON.parse(JSON.stringify(config))
    
    // Ensure default values for missing fields
    if (cleanConfig.trigger_condition) {
      const cond = cleanConfig.trigger_condition
      if (cond.metric === 'taker_volume') {
        cond.direction = cond.direction || 'any'
        cond.ratio_threshold = cond.ratio_threshold ?? 1.5
        cond.volume_threshold = cond.volume_threshold ?? 50000
        // Remove standard fields if present to avoid confusion
        delete cond.operator
        delete cond.threshold
      } else {
        cond.operator = cond.operator || 'greater_than'
        cond.threshold = cond.threshold ?? 0
      }
      cond.time_window = cond.time_window || '5m'
    }
    
    if (cleanConfig.signals) {
      cleanConfig.signals = cleanConfig.signals.map((s: any) => {
        const metric = s.metric || s.indicator
        if (metric === 'taker_volume') {
          return {
            ...s,
            metric: 'taker_volume',
            direction: s.direction || 'any',
            ratio_threshold: s.ratio_threshold ?? 1.5,
            volume_threshold: s.volume_threshold ?? 50000,
            time_window: s.time_window || '5m'
          }
        }
        return {
          ...s,
          metric: metric, // Ensure metric field is set
          operator: s.operator || 'greater_than',
          threshold: s.threshold ?? 0,
          time_window: s.time_window || '5m'
        }
      })
    }

    try {
      const success = isPool ? await onCreatePool(cleanConfig) : await onCreate(cleanConfig)
      if (success) {
        setCreatedSignals(prev => new Set(prev).add(signalKey))
      }
    } finally {
      setCreatingSignals(prev => {
        const next = new Set(prev)
        next.delete(signalKey)
        return next
      })
    }
  }

  return (
    <div className="w-[55%] flex flex-col bg-muted/30 min-h-0">
      <div className="shrink-0 p-4 border-b">
        <h3 className="text-sm font-semibold">{t('signals.aiGenerator.generatedSignals', 'Generated Signals')}</h3>
        <p className="text-xs text-muted-foreground mt-1">
          {configs.length > 0
            ? t('signals.aiGenerator.signalsCount', '{{count}} signal(s) generated').replace('{{count}}', configs.length.toString())
            : t('signals.aiGenerator.signalsWillAppear', 'AI-generated signals will appear here')}
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {configs.length > 0 ? (
          <div className="space-y-4">
            {configs.map((config, idx) => {
              const signalKey = config.name || `signal-${idx}`
              const isPool = config._type === 'pool'
              return isPool ? (
                <SignalPoolCard
                  key={idx}
                  config={config}
                  onCreate={() => handleCreate(config, true)}
                  getMetricLabel={getMetricLabel}
                  getOperatorLabel={getOperatorLabel}
                  isCreating={creatingSignals.has(signalKey)}
                  isCreated={createdSignals.has(signalKey)}
                  t={t}
                />
              ) : (
                <SignalCard
                  key={idx}
                  config={config}
                  onPreview={() => onPreview(config)}
                  onCreate={() => handleCreate(config, false)}
                  getMetricLabel={getMetricLabel}
                  getOperatorLabel={getOperatorLabel}
                  isCreating={creatingSignals.has(signalKey)}
                  isCreated={createdSignals.has(signalKey)}
                  t={t}
                />
              )
            })}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            <div className="text-center">
              <p className="text-sm">{t('signals.aiGenerator.noSignalsYet', 'No signals generated yet')}</p>
              <p className="text-xs mt-2">{t('signals.aiGenerator.startConversation', 'Start a conversation to generate signals')}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// Individual Signal Card Component
function SignalCard({
  config, onPreview, onCreate, getMetricLabel, getOperatorLabel, isCreating, isCreated, t
}: {
  config: SignalConfig
  onPreview: () => void
  onCreate: () => void
  getMetricLabel: (m: string) => string
  getOperatorLabel: (o: string) => string
  isCreating?: boolean
  isCreated?: boolean
  t: (key: string, fallback?: string) => string
}) {
  const cond = config.trigger_condition || {}
  const isTakerVolume = cond.metric === 'taker_volume'
  const hasValidMetric = cond.metric && typeof cond.metric === 'string'

  // Check if signal config is valid
  // Relaxed validation: as long as metric exists, we can use defaults
  const isValid = hasValidMetric

  return (
    <div className={`rounded-lg border bg-card p-4 ${!isValid ? 'border-destructive/50' : ''}`}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <h4 className="font-semibold text-sm">{config.name || t('signals.aiGenerator.unnamedSignal', 'Unnamed Signal')}</h4>
          <p className="text-xs text-muted-foreground">{config.symbol || t('signals.aiGenerator.noSymbol', 'No symbol')}</p>
        </div>
        {hasValidMetric ? (
          <span className="text-xs bg-primary/10 text-primary px-2 py-1 rounded">
            {getMetricLabel(cond.metric)}
          </span>
        ) : (
          <span className="text-xs bg-destructive/10 text-destructive px-2 py-1 rounded">
            {t('signals.aiGenerator.invalidConfig', 'Invalid Config')}
          </span>
        )}
      </div>
      {config.description && (
        <p className="text-xs text-muted-foreground mb-3">{config.description}</p>
      )}
      <div className="bg-muted/50 rounded p-2 mb-3">
        <div className="text-xs space-y-1">
          {!hasValidMetric ? (
            <div className="text-destructive">{t('signals.aiGenerator.missingMetric', 'Missing metric configuration')}</div>
          ) : isTakerVolume ? (
            <>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t('signals.aiGenerator.direction', 'Direction')}:</span>
                <span>{cond.direction || 'any'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t('signals.aiGenerator.ratioThreshold', 'Ratio Threshold')}:</span>
                <span>{cond.ratio_threshold || 1.5}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t('signals.aiGenerator.volumeThreshold', 'Volume Threshold')}:</span>
                <span>{cond.volume_threshold || 0}</span>
              </div>
            </>
          ) : (
            <>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t('signals.aiGenerator.metric', 'Metric')}:</span>
                <span>{getMetricLabel(cond.metric)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">{t('signals.aiGenerator.condition', 'Condition')}:</span>
                <span>{getOperatorLabel(cond.operator || '')} {cond.threshold}</span>
              </div>
            </>
          )}
          <div className="flex justify-between">
            <span className="text-muted-foreground">{t('signals.aiGenerator.timeWindow', 'Time Window')}:</span>
            <span>{cond.time_window || '5m'}</span>
          </div>
        </div>
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" className="flex-1" onClick={onPreview} disabled={!isValid}>
          {t('signals.aiGenerator.preview', 'Preview')}
        </Button>
        {isCreated ? (
          <Button size="sm" className="flex-1" variant="secondary" disabled>
            <span className="text-green-600">✓ {t('signals.aiGenerator.created', 'Created')}</span>
          </Button>
        ) : (
          <Button size="sm" className="flex-1" onClick={onCreate} disabled={!isValid || isCreating}>
            {isCreating ? t('signals.aiGenerator.creating', 'Creating...') : t('signals.aiGenerator.createSignal', 'Create Signal')}
          </Button>
        )}
      </div>
    </div>
  )
}

// Signal Pool Card Component
function SignalPoolCard({
  config, onCreate, getMetricLabel, getOperatorLabel, isCreating, isCreated, t
}: {
  config: SignalConfig
  onCreate: () => void
  getMetricLabel: (m: string) => string
  getOperatorLabel: (o: string) => string
  isCreating?: boolean
  isCreated?: boolean
  t: (key: string, fallback?: string) => string
}) {
  const signals = config.signals || []
  // Validate signals - support both 'metric' and 'indicator' field names (AI uses 'indicator')
  // Relaxed validation: as long as metric name exists, we allow creation (defaults applied on create)
  const isValid = signals.length > 0 && signals.every(s => {
    const metricName = s.metric || s.indicator  // AI outputs 'indicator', frontend uses 'metric'
    return !!metricName
  })

  return (
    <div className={`rounded-lg border bg-card p-4 ${!isValid ? 'border-destructive/50' : 'border-primary/50'}`}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <h4 className="font-semibold text-sm">{config.name || t('signals.aiGenerator.unnamedPool', 'Unnamed Pool')}</h4>
          <p className="text-xs text-muted-foreground">{config.symbol || t('signals.aiGenerator.noSymbol', 'No symbol')}</p>
        </div>
        <span className="text-xs bg-primary/20 text-primary px-2 py-1 rounded font-medium">
          {t('signals.aiGenerator.pool', 'Pool')} ({config.logic || 'AND'})
        </span>
      </div>
      {config.description && (
        <p className="text-xs text-muted-foreground mb-3">{config.description}</p>
      )}
      <div className="bg-muted/50 rounded p-2 mb-3">
        <div className="text-xs font-medium mb-2">
          {t('signals.aiGenerator.signalsCombined', '{{count}} Signal(s) Combined with {{logic}}:')
            .replace('{{count}}', signals.length.toString())
            .replace('{{logic}}', config.logic || 'AND')}
        </div>
        <div className="space-y-1">
          {signals.map((sig, idx) => {
            const metricName = sig.metric || sig.indicator  // AI outputs 'indicator', frontend uses 'metric'
            return (
              <div key={idx} className="text-xs flex items-center gap-2 bg-background/50 rounded px-2 py-1">
                <span className="font-medium">{getMetricLabel(metricName || '')}</span>
                {metricName === 'taker_volume' ? (
                  <span className="text-muted-foreground">
                    {sig.direction?.toUpperCase()} ≥{sig.ratio_threshold}x, ≥${((sig.volume_threshold || 0) / 1000000).toFixed(1)}M
                  </span>
                ) : (
                  <span className="text-muted-foreground">
                    {getOperatorLabel(sig.operator || '')} {sig.threshold}
                  </span>
                )}
                <span className="text-muted-foreground">({sig.time_window || '5m'})</span>
              </div>
            )
          })}
        </div>
      </div>
      <div className="flex gap-2">
        {isCreated ? (
          <Button size="sm" className="flex-1" variant="secondary" disabled>
            <span className="text-green-600">✓ {t('signals.aiGenerator.poolCreated', 'Pool Created')}</span>
          </Button>
        ) : (
          <Button size="sm" className="flex-1" onClick={onCreate} disabled={!isValid || isCreating}>
            {isCreating ? t('signals.aiGenerator.creating', 'Creating...') : t('signals.aiGenerator.createPool', 'Create Signal Pool')}
          </Button>
        )}
      </div>
    </div>
  )
}
