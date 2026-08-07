import { useState, useEffect } from 'react'
import toast from 'react-hot-toast'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Plus, Pencil, Bot, Wallet, ChevronDown, ChevronUp, Settings2, Check, Trash2 } from 'lucide-react'
import {
  getAccounts as getAccounts,
  createAccount as createAccount,
  updateAccount as updateAccount,
  deleteAccount as deleteAccount,
  testLLMConnection,
  checkBuilderAuthorization,
  approveBuilder,
  type TradingAccount,
  type TradingAccountCreate,
  type TradingAccountUpdate,
  type UnauthorizedAccount
} from '@/lib/api'
import UnifiedWalletConfigPanel from '@/components/trader/UnifiedWalletConfigPanel'
import { AuthorizationModal } from '@/components/hyperliquid'
import { useExchange } from '@/contexts/ExchangeContext'
import { Badge } from '@/components/ui/badge'
import { LLMConfigManager } from '@/components/settings'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface SettingsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAccountUpdated?: () => void  // Add callback for when account is updated
  embedded?: boolean  // Add embedded mode support
}

interface AIAccount extends TradingAccount {
  model?: string
  base_url?: string
  api_key?: string
  llm_config_id?: number | null
  llm_config_name?: string | null
  llm_config_id_deep?: number | null
  llm_config_name_deep?: string | null
}

interface AIAccountCreate extends TradingAccountCreate {
  model?: string
  base_url?: string
  api_key?: string
  llm_config_id?: number  // Changed from number | null to match base interface
  llm_config_id_deep?: number
}

// LLM Config from repository
interface LLMConfig {
  id: number
  name: string
  provider: string
  model: string
  base_url: string
  api_key_masked: string
  is_default: boolean
  is_active: boolean
}

// LLM Provider Presets
const LLM_PRESETS = [
  {
    name: 'OpenAI',
    model: 'gpt-4o',
    base_url: 'https://api.openai.com/v1',
    placeholder: 'sk-...'
  },
  {
    name: 'Deepseek',
    model: 'deepseek-chat',
    base_url: 'https://api.deepseek.com',
    placeholder: 'sk-...'
  },
  {
    name: '通义千问 (Qwen)',
    model: 'qwen-plus',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    placeholder: 'sk-...'
  },
  {
    name: '火山引擎 (Volcengine)',
    model: 'ep-******-*****',
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    placeholder: '火山引擎 API Key'
  },
  {
    name: '自定义 (Custom)',
    model: '',
    base_url: '',
    placeholder: 'API Key'
  }
]

// Configuration section type
type ConfigSection = 'config-repo' | 'llm' | 'wallet'

export default function SettingsDialog({ open, onOpenChange, onAccountUpdated, embedded = false }: SettingsDialogProps) {
  const { currentExchange, exchanges } = useExchange()
  const currentExchangeInfo = exchanges.find(ex => ex.id === currentExchange)
  
  const [accounts, setAccounts] = useState<AIAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [toggleLoadingId, setToggleLoadingId] = useState<number | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [unauthorizedAccounts, setUnauthorizedAccounts] = useState<UnauthorizedAccount[]>([])
  const [selectedPreset, setSelectedPreset] = useState(0) // OpenAI default
  const [editSelectedPreset, setEditSelectedPreset] = useState(0)
  // Configuration section tab
  const [configSection, setConfigSection] = useState<ConfigSection>('config-repo')
  // Expanded account for wallet config
  const [expandedAccountId, setExpandedAccountId] = useState<number | null>(null)
  
  // LLM Config library states
  const [llmConfigs, setLlmConfigs] = useState<LLMConfig[]>([])
  const [selectedLLMConfigId, setSelectedLLMConfigId] = useState<number | null>(null)
  const [selectedLLMConfigIdDeep, setSelectedLLMConfigIdDeep] = useState<number | null>(null)
  const [useConfigLibrary, setUseConfigLibrary] = useState(true) // Default to use config library
  const [editUseConfigLibrary, setEditUseConfigLibrary] = useState(false) // For edit form
  const [editSelectedLLMConfigId, setEditSelectedLLMConfigId] = useState<number | null>(null)
  const [editSelectedLLMConfigIdDeep, setEditSelectedLLMConfigIdDeep] = useState<number | null>(null)
  
  const [newAccount, setNewAccount] = useState<AIAccountCreate>({
    name: '',
    model: '',
    base_url: '',
    api_key: 'default-key-please-update-in-settings',
    auto_trading_enabled: true,
  })
  const [editAccount, setEditAccount] = useState<AIAccountCreate>({
    name: '',
    model: '',
    base_url: '',
    api_key: 'default-key-please-update-in-settings',
    auto_trading_enabled: true,
  })

  const loadAccounts = async () => {
    try {
      setLoading(true)
      const data = await getAccounts()
      setAccounts(data)
    } catch (error) {
      console.error('Failed to load accounts:', error)
      toast.error('Failed to load AI traders')
    } finally {
      setLoading(false)
    }
  }

  // Load LLM configurations from repository
  const loadLLMConfigs = async () => {
    try {
      const response = await fetch('/api/llm-configs')
      const data = await response.json()
      setLlmConfigs(data.items || [])
      // Set default selected config
      const defaultConfig = data.items?.find((c: LLMConfig) => c.is_default)
      if (defaultConfig && !selectedLLMConfigId) {
        setSelectedLLMConfigId(defaultConfig.id)
        setSelectedLLMConfigIdDeep(defaultConfig.id)
      }
    } catch (error) {
      console.error('Failed to load LLM configs:', error)
      // Don't show error toast as config library is optional
    }
  }

  useEffect(() => {
    if (open) {
      loadAccounts()
      loadLLMConfigs()
      setError(null)
      setTestResult(null)
      setShowAddForm(false)
      setEditingId(null)
    }
  }, [open])

  const handleCreateAccount = async () => {
    try {
      setLoading(true)
      setTesting(true)
      setError(null)
      setTestResult(null)

      if (!newAccount.name || !newAccount.name.trim()) {
        setError('Trader name is required')
        setLoading(false)
        setTesting(false)
        return
      }

      // Prepare account data based on configuration source
      const accountData: AIAccountCreate = useConfigLibrary 
        ? { 
            ...newAccount, 
            llm_config_id: selectedLLMConfigId || undefined,
            llm_config_id_deep: selectedLLMConfigIdDeep || undefined,
            api_key: undefined // Don't send API key when using config library
          }
        : { 
            ...newAccount, 
            llm_config_id: undefined,
            llm_config_id_deep: undefined 
          }

      // If using manual input and AI fields are provided, test LLM connection first
      if (!useConfigLibrary && (accountData.model || accountData.base_url || accountData.api_key)) {
        setTestResult('Testing LLM connection...')
        try {
          const testResponse = await testLLMConnection({
            model: accountData.model,
            base_url: accountData.base_url,
            api_key: accountData.api_key,
          })
          if (!testResponse.success) {
            const message = testResponse.message || 'LLM connection test failed'
            setError(`LLM Test Failed: ${message}`)
            setTestResult(`❌ Test failed: ${message}`)
            setLoading(false)
            setTesting(false)
            return
          }
          setTestResult('✅ LLM connection test passed! Creating AI trader...')
        } catch (testError) {
          const message = testError instanceof Error ? testError.message : 'LLM connection test failed'
          setError(`LLM Test Failed: ${message}`)
          setTestResult(`❌ Test failed: ${message}`)
          setLoading(false)
          setTesting(false)
          return
        }
      } else if (useConfigLibrary && selectedLLMConfigId) {
        setTestResult('Using configuration from library...')
      }

      console.log('Creating account with data:', accountData)
      await createAccount(accountData)
      setNewAccount({ name: '', model: '', base_url: '', api_key: 'default-key-please-update-in-settings', auto_trading_enabled: true })
      setShowAddForm(false)
      await loadAccounts()

      toast.success('AI trader created successfully!')

      // Notify parent component that account was created
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to create account:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to create AI trader'
      setError(errorMessage)
      toast.error(`Failed to create AI trader: ${errorMessage}`)
    } finally {
      setLoading(false)
      setTesting(false)
      setTestResult(null)
    }
  }

  const handleUpdateAccount = async () => {
    if (!editingId) return
    try {
      setLoading(true)
      setTesting(true)
      setError(null)
      setTestResult(null)
      
      if (!editAccount.name || !editAccount.name.trim()) {
        setError('Trader name is required')
        setLoading(false)
        setTesting(false)
        return
      }

      // Prepare account data based on configuration source
      const accountData: AIAccountCreate = editUseConfigLibrary 
        ? { 
            ...editAccount, 
            llm_config_id: editSelectedLLMConfigId || undefined,
            llm_config_id_deep: editSelectedLLMConfigIdDeep || undefined,
            api_key: undefined // Don't send API key when using config library
          }
        : { 
            ...editAccount, 
            llm_config_id: undefined,
            llm_config_id_deep: undefined 
          }
      
      // Test LLM connection first if using manual input and AI model data is provided
      if (!editUseConfigLibrary && (accountData.model || accountData.base_url || accountData.api_key)) {
        setTestResult('Testing LLM connection...')
        
        try {
          const testResponse = await testLLMConnection({
            model: accountData.model,
            base_url: accountData.base_url,
            api_key: accountData.api_key
          })
          
          if (!testResponse.success) {
            setError(`LLM Test Failed: ${testResponse.message}`)
            setTestResult(`❌ Test failed: ${testResponse.message}`)
            setLoading(false)
            setTesting(false)
            return
          }
          
          setTestResult('✅ LLM connection test passed!')
        } catch (testError) {
          const errorMessage = testError instanceof Error ? testError.message : 'LLM connection test failed'
          setError(`LLM Test Failed: ${errorMessage}`)
          setTestResult(`❌ Test failed: ${errorMessage}`)
          setLoading(false)
          setTesting(false)
          return
        }
      } else if (editUseConfigLibrary && editSelectedLLMConfigId) {
        setTestResult('Using configuration from library...')
      }
      
      setTesting(false)
      setTestResult('Test passed! Saving AI trader...')

      console.log('Updating account with data:', accountData)
      await updateAccount(editingId, accountData)
      setEditingId(null)
      setEditAccount({ name: '', model: '', base_url: '', api_key: '', auto_trading_enabled: true })
      setEditUseConfigLibrary(false)
      setEditSelectedLLMConfigId(null)
      setTestResult(null)
      await loadAccounts()
      
      toast.success('AI trader updated successfully!')
      
      // Notify parent component that account was updated
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to update account:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to update AI trader'
      setError(errorMessage)
      setTestResult(null)
      toast.error(`Failed to update AI trader: ${errorMessage}`)
    } finally {
      setLoading(false)
      setTesting(false)
    }
  }

  const startEdit = (account: AIAccount) => {
    setEditingId(account.id)
    setEditAccount({
      name: account.name,
      model: account.model || '',
      base_url: account.base_url || '',
      api_key: account.api_key || '',
      auto_trading_enabled: account.auto_trading_enabled ?? true,
      llm_config_id: account.llm_config_id || undefined,
      llm_config_id_deep: account.llm_config_id_deep || undefined,
    })
    // Set edit form config library state
    if (account.llm_config_id) {
      setEditUseConfigLibrary(true)
      setEditSelectedLLMConfigId(account.llm_config_id)
      setEditSelectedLLMConfigIdDeep(account.llm_config_id_deep || null)
    } else {
      setEditUseConfigLibrary(false)
      setEditSelectedLLMConfigId(null)
      setEditSelectedLLMConfigIdDeep(null)
    }
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditAccount({ name: '', model: '', base_url: '', api_key: 'default-key-please-update-in-settings', auto_trading_enabled: true })
    setEditUseConfigLibrary(false)
    setEditSelectedLLMConfigId(null)
    setEditSelectedLLMConfigIdDeep(null)
    setTestResult(null)
    setError(null)
  }

  const handleDeleteAccount = async (accountId: number) => {
    try {
      setLoading(true)
      await deleteAccount(accountId)
      await loadAccounts()
      setDeleteConfirmId(null)
      toast.success('AI交易员已删除')
      
      // Notify parent component that account was deleted
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to delete account:', error)
      const errorMessage = error instanceof Error ? error.message : '删除失败'
      toast.error(`删除AI交易员失败: ${errorMessage}`)
    } finally {
      setLoading(false)
    }
  }

  // Handle preset selection for new account
  const handlePresetChange = (index: number) => {
    setSelectedPreset(index)
    const preset = LLM_PRESETS[index]
    setNewAccount({
      ...newAccount,
      model: preset.model,
      base_url: preset.base_url,
      api_key: ''
    })
  }

  // Handle preset selection for edit account
  const handleEditPresetChange = (index: number) => {
    setEditSelectedPreset(index)
    const preset = LLM_PRESETS[index]
    setEditAccount({
      ...editAccount,
      model: preset.model,
      base_url: preset.base_url
    })
  }

  const handleToggleAutoTrading = async (account: AIAccount, nextValue: boolean) => {
    try {
      setToggleLoadingId(account.id)

      // If enabling trading and account has mainnet wallet, check authorization first
      if (nextValue && account.has_mainnet_wallet && account.wallet_address) {
        const authStatus = await checkBuilderAuthorization(account.wallet_address)
        if (!authStatus.authorized) {
          // Builder binding - try to bind builder
          try {
            const authResult = await approveBuilder(account.id)
            // Check if binding failed
            if (!authResult.success || authResult.result?.status === 'err') {
              // Show authorization modal for user to retry manually
              setUnauthorizedAccounts([{
                account_id: account.id,
                account_name: account.name,
                wallet_address: account.wallet_address,
                max_fee: authStatus.max_fee,
                required_fee: authStatus.required_fee
              }])
              setAuthModalOpen(true)
              setToggleLoadingId(null)
              return  // Don't enable trading if binding failed
            }
          } catch (err) {
            console.error(`Builder binding failed for account ${account.id}:`, err)
            // Show modal on exception as well
            setUnauthorizedAccounts([{
              account_id: account.id,
              account_name: account.name,
              wallet_address: account.wallet_address,
              max_fee: authStatus.max_fee,
              required_fee: authStatus.required_fee
            }])
            setAuthModalOpen(true)
            setToggleLoadingId(null)
            return
          }
        }
      }

      await updateAccount(account.id, { auto_trading_enabled: nextValue })
      setAccounts((prev) =>
        prev.map((acc) => (acc.id === account.id ? { ...acc, auto_trading_enabled: nextValue } : acc))
      )
      toast.success(nextValue ? `Auto trading enabled for ${account.name}` : `Auto trading paused for ${account.name}`)
      onAccountUpdated?.()
    } catch (error) {
      console.error('Failed to toggle auto trading:', error)
      const errorMessage = error instanceof Error ? error.message : 'Failed to update trading status'
      toast.error(errorMessage)
    } finally {
      setToggleLoadingId(null)
    }
  }

  const handleAuthorizationComplete = async () => {
    setAuthModalOpen(false)
    // After authorization complete, enable trading for the authorized accounts
    for (const account of unauthorizedAccounts) {
      try {
        await updateAccount(account.account_id, { auto_trading_enabled: true })
        setAccounts((prev) =>
          prev.map((acc) => (acc.id === account.account_id ? { ...acc, auto_trading_enabled: true } : acc))
        )
        toast.success(`Auto trading enabled for ${account.account_name}`)
      } catch (error) {
        console.error(`Failed to enable trading for ${account.account_name}:`, error)
      }
    }
    setUnauthorizedAccounts([])
    onAccountUpdated?.()
  }

  const handleAuthModalClose = () => {
    setAuthModalOpen(false)
    setUnauthorizedAccounts([])
    loadAccounts() // Reload to get updated trading status
  }

  const content = (
    <>
      {!embedded && (
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            AI Trader Management
            <Badge variant="outline" className="text-xs font-normal">
              {currentExchangeInfo?.displayName || currentExchange}
            </Badge>
          </DialogTitle>
          <DialogDescription>
            管理您的AI交易员及其配置。大模型API和钱包API已分离到不同区域。
          </DialogDescription>
        </DialogHeader>
      )}

      <Tabs value={configSection} onValueChange={(v) => setConfigSection(v as ConfigSection)} className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="config-repo" className="flex items-center gap-2">
            <Settings2 className="h-4 w-4" />
            配置库
          </TabsTrigger>
          <TabsTrigger value="llm" className="flex items-center gap-2">
            <Bot className="h-4 w-4" />
            AI交易员
          </TabsTrigger>
          <TabsTrigger value="wallet" className="flex items-center gap-2">
            <Wallet className="h-4 w-4" />
            钱包配置
          </TabsTrigger>
        </TabsList>

        {/* LLM Configuration Repository Tab */}
        <TabsContent value="config-repo" className="space-y-4">
          <LLMConfigManager />
        </TabsContent>

        {/* LLM Configuration Tab */}
        <TabsContent value="llm" className="space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded">
              {error}
            </div>
          )}

          <div className="flex items-center justify-between">
            <Button
              onClick={() => setShowAddForm(!showAddForm)}
              size="sm"
              className="flex items-center gap-2"
            >
              <Plus className="h-4 w-4" />
              添加 AI 交易员
            </Button>
          </div>

          <div className="space-y-3 max-h-[400px] overflow-y-auto">
            {/* Add New Account Form */}
            {showAddForm && (
              <div className="space-y-4 border rounded-lg p-4 bg-muted/50">
                <h3 className="text-lg font-medium">添加新的 AI 交易员</h3>
                <div className="space-y-3">
                  {/* Configuration Source Selector */}
                  <div className="flex items-center gap-4 p-3 bg-background rounded border">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        checked={useConfigLibrary}
                        onChange={() => {
                          setUseConfigLibrary(true)
                          // Auto-fill with selected config if available
                          if (selectedLLMConfigId) {
                            const config = llmConfigs.find(c => c.id === selectedLLMConfigId)
                            if (config) {
                              setNewAccount({
                                ...newAccount,
                                llm_config_id: config.id,
                                model: config.model,
                                base_url: config.base_url,
                                api_key: '' // Don't copy API key for security
                              })
                            }
                          }
                        }}
                      />
                      <span className="text-sm font-medium">从配置库选择</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        checked={!useConfigLibrary}
                        onChange={() => {
                          setUseConfigLibrary(false)
                          setNewAccount({
                            ...newAccount,
                            llm_config_id: undefined
                          })
                        }}
                      />
                      <span className="text-sm font-medium">手动输入配置</span>
                    </label>
                  </div>

                  {/* Config Library Selector */}
                  {useConfigLibrary && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium">选择大模型配置</label>
                      {llmConfigs.length > 0 ? (
                        <Select
                          value={selectedLLMConfigId?.toString() || ''}
                          onValueChange={(value) => {
                            const configId = parseInt(value)
                            setSelectedLLMConfigId(configId)
                            const config = llmConfigs.find(c => c.id === configId)
                            if (config) {
                              setNewAccount({
                                ...newAccount,
                                llm_config_id: config.id,
                                model: config.model,
                                base_url: config.base_url,
                                api_key: '' // Will use the config's API key
                              })
                            }
                          }}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="选择配置..." />
                          </SelectTrigger>
                          <SelectContent>
                            {llmConfigs
                              .filter(config => config.is_active)
                              .map(config => (
                                <SelectItem key={config.id} value={config.id.toString()}>
                                  <div className="flex items-center gap-2">
                                    <span>{config.name}</span>
                                    <span className="text-xs text-muted-foreground">({config.provider})</span>
                                    {config.is_default && (
                                      <Badge variant="outline" className="text-xs">默认</Badge>
                                    )}
                                  </div>
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <div className="text-sm text-muted-foreground p-3 bg-muted rounded border">
                          暂无可用配置，请先在"配置库"标签页中创建配置
                        </div>
                      )}
                      {selectedLLMConfigId && (
                        <div className="text-xs text-muted-foreground p-2 bg-blue-50 border border-blue-200 rounded">
                          将使用配置库中的API密钥，无需重复输入
                        </div>
                      )}
                    </div>
                  )}

                  {/* Deep Reasoning Config Selector */}
                  {useConfigLibrary && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium">深度推理配置 <span className="text-muted-foreground">(可选)</span></label>
                      <p className="text-xs text-muted-foreground">策略分析 · 多熊辩论 · 综合决策</p>
                      {llmConfigs.length > 0 ? (
                        <Select
                          value={selectedLLMConfigIdDeep?.toString() || ''}
                          onValueChange={(value) => {
                            const configId = parseInt(value)
                            setSelectedLLMConfigIdDeep(configId)
                          }}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="默认同快速模型..." />
                          </SelectTrigger>
                          <SelectContent>
                            {llmConfigs
                              .filter(config => config.is_active)
                              .map(config => (
                                <SelectItem key={config.id} value={config.id.toString()}>
                                  <div className="flex items-center gap-2">
                                    <span>{config.name}</span>
                                    <span className="text-xs text-muted-foreground">({config.provider})</span>
                                    {config.is_default && (
                                      <Badge variant="outline" className="text-xs">默认</Badge>
                                    )}
                                  </div>
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <div className="text-sm text-muted-foreground p-3 bg-muted rounded border">
                          暂无可用配置
                        </div>
                      )}
                    </div>
                  )}

                  {/* LLM Provider Preset Selector - only show when manual input */}
                  {!useConfigLibrary && (
                    <div className="space-y-2">
                      <label className="text-sm font-medium">选择大模型提供商</label>
                      <div className="grid grid-cols-2 gap-2">
                        {LLM_PRESETS.map((preset, index) => (
                          <button
                            key={preset.name}
                            type="button"
                            onClick={() => handlePresetChange(index)}
                            className={`px-3 py-2 text-sm rounded border transition-colors ${
                              selectedPreset === index
                                ? 'bg-primary text-primary-foreground border-primary'
                                : 'bg-background hover:bg-muted border-border'
                            }`}
                          >
                            {preset.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <Input
                      placeholder="交易员名称"
                      value={newAccount.name || ''}
                      onChange={(e) => setNewAccount({ ...newAccount, name: e.target.value })}
                    />
                    <Input
                      placeholder="Model (e.g., gpt-4)"
                      value={newAccount.model || ''}
                      onChange={(e) => setNewAccount({ ...newAccount, model: e.target.value })}
                      disabled={useConfigLibrary}
                      className={useConfigLibrary ? 'bg-muted' : ''}
                    />
                  </div>
                  <Input
                    placeholder="Base URL (e.g., https://api.openai.com/v1)"
                    value={newAccount.base_url || ''}
                    onChange={(e) => setNewAccount({ ...newAccount, base_url: e.target.value })}
                    disabled={useConfigLibrary}
                    className={useConfigLibrary ? 'bg-muted' : ''}
                  />
                  {!useConfigLibrary && (
                    <Input
                      placeholder={LLM_PRESETS[selectedPreset].placeholder}
                      type="password"
                      value={newAccount.api_key || ''}
                      onChange={(e) => setNewAccount({ ...newAccount, api_key: e.target.value })}
                    />
                  )}
                  <label className="flex items-center gap-2 text-sm text-muted-foreground">
                    <input
                      type="checkbox"
                      className="h-4 w-4"
                      checked={newAccount.auto_trading_enabled ?? true}
                      onChange={(e) => setNewAccount({ ...newAccount, auto_trading_enabled: e.target.checked })}
                    />
                    <span>启用自动交易</span>
                  </label>
                  <div className="flex gap-2">
                    <Button onClick={handleCreateAccount} disabled={loading}>
                      测试并创建
                    </Button>
                    <Button variant="outline" onClick={() => setShowAddForm(false)}>
                      取消
                    </Button>
                  </div>
                  {testResult && (
                    <div className="text-sm text-muted-foreground">
                      {testResult}
                    </div>
                  )}
                </div>
              </div>
            )}

            {loading && accounts.length === 0 ? (
              <div className="text-center py-4">正在加载 AI 交易员...</div>
            ) : accounts.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                <Bot className="h-12 w-12 mx-auto mb-2 opacity-50" />
                <p>暂无 AI 交易员</p>
                <p className="text-xs">点击上方按钮添加您的第一个 AI 交易员</p>
              </div>
            ) : (
              accounts.map((account) => (
                <div key={account.id} className="border rounded-lg p-4 space-y-3">
                  {editingId === account.id ? (
                    <div className="space-y-3">
                      {/* Configuration Source Selector */}
                      <div className="flex items-center gap-4 p-3 bg-background rounded border">
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            checked={editUseConfigLibrary}
                            onChange={() => {
                              setEditUseConfigLibrary(true)
                              // Auto-fill with selected config if available
                              if (editSelectedLLMConfigId) {
                                const config = llmConfigs.find(c => c.id === editSelectedLLMConfigId)
                                if (config) {
                                  setEditAccount({
                                    ...editAccount,
                                    llm_config_id: config.id,
                                    model: config.model,
                                    base_url: config.base_url,
                                    api_key: ''
                                  })
                                }
                              }
                            }}
                          />
                          <span className="text-sm font-medium">从配置库选择</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            checked={!editUseConfigLibrary}
                            onChange={() => {
                              setEditUseConfigLibrary(false)
                              setEditAccount({
                                ...editAccount,
                                llm_config_id: undefined
                              })
                            }}
                          />
                          <span className="text-sm font-medium">手动输入配置</span>
                        </label>
                      </div>

                      {/* Config Library Selector for Edit */}
                      {editUseConfigLibrary && (
                        <div className="space-y-2">
                          <label className="text-sm font-medium">选择大模型配置</label>
                          {llmConfigs.length > 0 ? (
                            <Select
                              value={editSelectedLLMConfigId?.toString() || ''}
                              onValueChange={(value) => {
                                const configId = parseInt(value)
                                setEditSelectedLLMConfigId(configId)
                                const config = llmConfigs.find(c => c.id === configId)
                                if (config) {
                                  setEditAccount({
                                    ...editAccount,
                                    llm_config_id: config.id,
                                    model: config.model,
                                    base_url: config.base_url,
                                    api_key: ''
                                  })
                                }
                              }}
                            >
                              <SelectTrigger>
                                <SelectValue placeholder="选择配置..." />
                              </SelectTrigger>
                              <SelectContent>
                                {llmConfigs
                                  .filter(config => config.is_active)
                                  .map(config => (
                                    <SelectItem key={config.id} value={config.id.toString()}>
                                      <div className="flex items-center gap-2">
                                        <span>{config.name}</span>
                                        <span className="text-xs text-muted-foreground">({config.provider})</span>
                                        {config.is_default && (
                                          <Badge variant="outline" className="text-xs">默认</Badge>
                                        )}
                                      </div>
                                    </SelectItem>
                                  ))}
                              </SelectContent>
                            </Select>
                          ) : (
                            <div className="text-sm text-muted-foreground p-3 bg-muted rounded border">
                              暂无可用配置，请先在"配置库"标签页中创建配置
                            </div>
                          )}
                        </div>
                      )}

                      {/* Deep Reasoning Config Selector for Edit */}
                      {editUseConfigLibrary && (
                        <div className="space-y-2">
                          <label className="text-sm font-medium">深度推理配置 <span className="text-muted-foreground">(可选)</span></label>
                          <p className="text-xs text-muted-foreground">策略分析 · 多熊辩论 · 综合决策</p>
                          {llmConfigs.length > 0 ? (
                            <Select
                              value={editSelectedLLMConfigIdDeep?.toString() || ''}
                              onValueChange={(value) => {
                                const configId = parseInt(value)
                                setEditSelectedLLMConfigIdDeep(configId)
                              }}
                            >
                              <SelectTrigger>
                                <SelectValue placeholder="默认同快速模型..." />
                              </SelectTrigger>
                              <SelectContent>
                                {llmConfigs
                                  .filter(config => config.is_active)
                                  .map(config => (
                                    <SelectItem key={config.id} value={config.id.toString()}>
                                      <div className="flex items-center gap-2">
                                        <span>{config.name}</span>
                                        <span className="text-xs text-muted-foreground">({config.provider})</span>
                                        {config.is_default && (
                                          <Badge variant="outline" className="text-xs">默认</Badge>
                                        )}
                                      </div>
                                    </SelectItem>
                                  ))}
                              </SelectContent>
                            </Select>
                          ) : (
                            <div className="text-sm text-muted-foreground p-3 bg-muted rounded border">
                              暂无可用配置
                            </div>
                          )}
                        </div>
                      )}

                      {/* LLM Provider Preset Selector for Edit - only show when manual input */}
                      {!editUseConfigLibrary && (
                        <div className="space-y-2">
                          <label className="text-sm font-medium">选择大模型提供商</label>
                          <div className="grid grid-cols-2 gap-2">
                            {LLM_PRESETS.map((preset, index) => (
                              <button
                                key={preset.name}
                                type="button"
                                onClick={() => handleEditPresetChange(index)}
                                className={`px-3 py-2 text-sm rounded border transition-colors ${
                                  editSelectedPreset === index
                                    ? 'bg-primary text-primary-foreground border-primary'
                                    : 'bg-background hover:bg-muted border-border'
                                }`}
                              >
                                {preset.name}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="grid grid-cols-2 gap-3">
                        <Input
                          placeholder="交易员名称"
                          value={editAccount.name || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, name: e.target.value })}
                        />
                        <Input
                          placeholder="Model"
                          value={editAccount.model || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, model: e.target.value })}
                          disabled={editUseConfigLibrary}
                          className={editUseConfigLibrary ? 'bg-muted' : ''}
                        />
                      </div>
                      <Input
                        placeholder="Base URL"
                        value={editAccount.base_url || ''}
                        onChange={(e) => setEditAccount({ ...editAccount, base_url: e.target.value })}
                        disabled={editUseConfigLibrary}
                        className={editUseConfigLibrary ? 'bg-muted' : ''}
                      />
                      {!editUseConfigLibrary && (
                        <Input
                          placeholder="API Key"
                          type="password"
                          value={editAccount.api_key || ''}
                          onChange={(e) => setEditAccount({ ...editAccount, api_key: e.target.value })}
                        />
                      )}
                      <label className="flex items-center gap-2 text-sm text-muted-foreground">
                        <input
                          type="checkbox"
                          className="h-4 w-4"
                          checked={editAccount.auto_trading_enabled ?? true}
                          onChange={(e) => setEditAccount({ ...editAccount, auto_trading_enabled: e.target.checked })}
                        />
                        <span>启用自动交易</span>
                      </label>
                      {testResult && (
                        <div className={`text-xs p-2 rounded ${
                          testResult.includes('❌')
                            ? 'bg-red-50 text-red-700 border border-red-200'
                            : 'bg-green-50 text-green-700 border border-green-200'
                        }`}>
                          {testResult}
                        </div>
                      )}
                      <div className="flex gap-2">
                        <Button onClick={handleUpdateAccount} disabled={loading || testing} size="sm">
                          {testing ? '测试中...' : '测试并保存'}
                        </Button>
                        <Button onClick={cancelEdit} variant="outline" size="sm" disabled={loading || testing}>
                          取消
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center justify-between gap-4">
                      <div className="space-y-1 flex-1">
                        <div className="flex items-center justify-between gap-3">
                          <div className="font-medium">{account.name}</div>
                          <label className="flex items-center gap-2 text-xs text-muted-foreground whitespace-nowrap">
                            <input
                              type="checkbox"
                              className="h-4 w-4"
                              checked={account.auto_trading_enabled ?? true}
                              disabled={toggleLoadingId === account.id || loading}
                              onChange={(e) => handleToggleAutoTrading(account, e.target.checked)}
                            />
                            <span>自动交易</span>
                          </label>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {account.model ? `Model: ${account.model}` : '未配置模型'}
                        </div>
                        {account.base_url && (
                          <div className="text-xs text-muted-foreground truncate">
                            Base URL: {account.base_url}
                          </div>
                        )}
                        {account.api_key && (
                          <div className="text-xs text-muted-foreground truncate max-w-full">
                            API Key: {'*'.repeat(Math.min(20, Math.max(0, (account.api_key?.length || 0) - 4)))}{account.api_key?.slice(-4) || '****'}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {deleteConfirmId === account.id ? (
                          <>
                            <Button
                              onClick={() => handleDeleteAccount(account.id)}
                              variant="destructive"
                              size="sm"
                              disabled={loading}
                            >
                              确认删除
                            </Button>
                            <Button
                              onClick={() => setDeleteConfirmId(null)}
                              variant="outline"
                              size="sm"
                              disabled={loading}
                            >
                              取消
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              onClick={() => startEdit(account)}
                              variant="outline"
                              size="sm"
                            >
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              onClick={() => setDeleteConfirmId(account.id)}
                              variant="outline"
                              size="sm"
                              className="text-red-600 hover:text-red-700 hover:bg-red-50"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          {/* LLM Config Info */}
          <div className="text-xs text-muted-foreground bg-blue-50 border border-blue-200 rounded p-2">
            <p className="font-medium text-blue-900 mb-1">💡 AI交易员说明</p>
            <p className="text-blue-800">
              在此管理AI交易员。新建交易员时，可直接输入大模型API信息，也可在"配置库"中预先创建配置后复用。
            </p>
          </div>
        </TabsContent>

        {/* Wallet Configuration Tab */}
        <TabsContent value="wallet" className="space-y-4">
          {accounts.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Wallet className="h-12 w-12 mx-auto mb-2 opacity-50" />
              <p>请先在"大模型配置"中添加 AI 交易员</p>
              <p className="text-xs">添加 AI 交易员后，可在此处配置其钱包</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setConfigSection('llm')}
              >
                去添加 AI 交易员
              </Button>
            </div>
          ) : (
            <div className="space-y-4 max-h-[450px] overflow-y-auto">
              {accounts.map((account) => (
                <div key={account.id} className="border rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <Bot className="h-4 w-4" />
                    <span className="font-medium">{account.name}</span>
                    <Badge variant={account.auto_trading_enabled ? 'default' : 'secondary'} className="text-xs">
                      {account.auto_trading_enabled ? '交易中' : '已暂停'}
                    </Badge>
                  </div>
                  
                  {/* Unified Wallet Config based on current exchange */}
                  <UnifiedWalletConfigPanel
                    accountId={account.id}
                    accountName={account.name}
                    onWalletConfigured={loadAccounts}
                  />
                </div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </>
  )

  if (embedded) {
    return (
      <>
        {content}
        <AuthorizationModal
          isOpen={authModalOpen}
          onClose={handleAuthModalClose}
          unauthorizedAccounts={unauthorizedAccounts}
          onAuthorizationComplete={handleAuthorizationComplete}
        />
      </>
    )
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-[600px]">
          {content}
        </DialogContent>
      </Dialog>
      <AuthorizationModal
        isOpen={authModalOpen}
        onClose={handleAuthModalClose}
        unauthorizedAccounts={unauthorizedAccounts}
        onAuthorizationComplete={handleAuthorizationComplete}
      />
    </>
  )
}

export { SettingsDialog }
