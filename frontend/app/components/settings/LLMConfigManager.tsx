'use client'

import { useState, useEffect } from 'react'
import { Plus, Edit2, Trash2, Check, X, RefreshCw, Star, Settings2, Merge } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import toast from 'react-hot-toast'

const DEEPSEEK_FLASH = 'deepseek-v4-flash'

function isFlashModelName(model: string): boolean {
  const m = (model || '').toLowerCase()
  return (m.includes('flash') || m.includes('chat')) && !m.includes('pro') && !m.includes('reasoner')
}

function isProModelName(model: string): boolean {
  // 历史 Pro/reasoner 配置仍识别；新建一律 flash
  const m = (model || '').toLowerCase()
  return m.includes('pro') || m.includes('reasoner') || m.includes('r1')
}

function inferDeepseekToggles(config: LLMConfiguration): { enableFlash: boolean; enablePro: boolean } {
  const model = config.model || ''
  const modelDeep = config.model_deep || ''
  const hasFlash = isFlashModelName(model) || isFlashModelName(modelDeep)
  const hasPro = isProModelName(model) || isProModelName(modelDeep)
  return {
    enableFlash: hasFlash || Boolean(model) || !hasPro,
    // 深度档位也统一 flash，不再默认勾选 Pro
    enablePro: false,
  }
}

interface LLMConfiguration {
  id: number
  name: string
  provider: string
  description: string | null
  model: string
  model_deep?: string | null
  base_url: string
  api_key_masked: string
  is_default: boolean
  is_active: boolean
  last_tested_at: string | null
  test_status: string | null
  test_message: string | null
  usage_count: number
  last_used_at: string | null
  accounts_count: number
  profiles_count?: number
  created_at: string
  updated_at: string
}

interface ModelVariant {
  value: string
  label: string
  tier: string
}

interface LLMProvider {
  id: string
  name: string
  default_model: string
  default_base_url: string
  description: string
  key_placeholder: string
  model_variants?: ModelVariant[]
  dual_model?: boolean
  dual_model_hint?: string
}

interface LLMConfigManagerProps {
  onConfigSelected?: (configId: number) => void
  selectionMode?: boolean
  selectedConfigId?: number | null
}

const API_BASE = '/api/llm-configs'

async function fetchLLMConfigs(all: boolean = false): Promise<LLMConfiguration[]> {
  const endpoint = all ? `${API_BASE}/all` : API_BASE
  const response = await fetch(endpoint)
  if (!response.ok) throw new Error('Failed to fetch LLM configurations')
  const data = await response.json()
  return data.items || []
}

async function fetchProviders(): Promise<LLMProvider[]> {
  const response = await fetch(`${API_BASE}/providers`)
  if (!response.ok) throw new Error('Failed to fetch providers')
  const data = await response.json()
  return data.providers || []
}

async function createLLMConfig(config: Record<string, unknown>): Promise<LLMConfiguration> {
  const response = await fetch(API_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || 'Failed to create configuration')
  }
  return response.json()
}

async function updateLLMConfig(id: number, config: Record<string, unknown>): Promise<LLMConfiguration> {
  const response = await fetch(`${API_BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || 'Failed to update configuration')
  }
  return response.json()
}

async function deleteLLMConfig(id: number, force = false): Promise<void> {
  const url = force ? `${API_BASE}/${id}?force=true` : `${API_BASE}/${id}`
  const response = await fetch(url, { method: 'DELETE' })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const detail = data.detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join('; ')
        : 'Failed to delete configuration'
    throw new Error(message)
  }
}

function formatRefs(config: LLMConfiguration): string {
  const parts: string[] = []
  if (config.accounts_count) parts.push(`${config.accounts_count} 个交易员`)
  if (config.profiles_count) parts.push(`${config.profiles_count} 个套利档案`)
  return parts.length ? parts.join('、') : '无引用'
}

async function consolidateDeepseek(): Promise<{ groups_merged?: number; configs_deleted?: number }> {
  const response = await fetch(`${API_BASE}/consolidate-deepseek`, { method: 'POST' })
  if (!response.ok) throw new Error('合并失败')
  return response.json()
}

async function testLLMConfig(id: number): Promise<{ success: boolean; message: string }> {
  const response = await fetch(`${API_BASE}/${id}/test`, { method: 'POST' })
  if (!response.ok) throw new Error('Failed to test configuration')
  return response.json()
}

async function setDefaultConfig(id: number): Promise<void> {
  const response = await fetch(`${API_BASE}/${id}/set-default`, { method: 'POST' })
  if (!response.ok) throw new Error('Failed to set default configuration')
}

export default function LLMConfigManager({
  onConfigSelected,
  selectionMode = false,
  selectedConfigId = null,
}: LLMConfigManagerProps) {
  const [configs, setConfigs] = useState<LLMConfiguration[]>([])
  const [providers, setProviders] = useState<LLMProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddForm, setShowAddForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [selectedProvider, setSelectedProvider] = useState(0)
  const [merging, setMerging] = useState(false)

  const [formData, setFormData] = useState({
    name: '',
    description: '',
    model: '',
    model_deep: '',
    base_url: '',
    api_key: '',
    is_default: false,
    enableFlash: true,
    enablePro: true,
  })

  const currentProvider = providers[selectedProvider]
  const isDeepseekDual = currentProvider?.id === 'deepseek'

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      setLoading(true)
      const [configsData, providersData] = await Promise.all([
        fetchLLMConfigs(true),
        fetchProviders(),
      ])
      setConfigs(configsData)
      setProviders(providersData)
    } catch (error) {
      console.error('Failed to load data:', error)
      toast.error('加载配置失败')
    } finally {
      setLoading(false)
    }
  }

  const handleProviderChange = (index: number) => {
    setSelectedProvider(index)
    const provider = providers[index]
    if (provider) {
      setFormData((prev) => ({
        ...prev,
        model: provider.default_model || DEEPSEEK_FLASH,
        model_deep: provider.id === 'deepseek' ? DEEPSEEK_FLASH : '',
        base_url: provider.default_base_url,
        enableFlash: provider.id === 'deepseek',
        enablePro: false,
      }))
    }
  }

  const buildModelPayload = () => {
    if (isDeepseekDual) {
      if (!formData.enableFlash && !formData.enablePro) {
        throw new Error('请勾选启用 DeepSeek V4 Flash')
      }
      // 统一 flash：深度任务也走同一模型
      return { model: DEEPSEEK_FLASH, model_deep: DEEPSEEK_FLASH }
    }
    if (!formData.model.trim()) throw new Error('请填写模型名称')
    return { model: formData.model.trim(), model_deep: formData.model_deep || undefined }
  }

  const handleCreate = async () => {
    if (!formData.name || !formData.base_url || !formData.api_key) {
      toast.error('请填写名称、Base URL 和 API Key')
      return
    }
    try {
      const { model, model_deep } = buildModelPayload()
      const provider = providers[selectedProvider]
      await createLLMConfig({
        name: formData.name,
        provider: provider?.id || 'custom',
        description: formData.description || undefined,
        model,
        model_deep,
        base_url: formData.base_url,
        api_key: formData.api_key,
        is_default: formData.is_default,
      })
      toast.success('配置创建成功')
      setShowAddForm(false)
      resetForm()
      loadData()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '创建配置失败')
    }
  }

  const handleUpdate = async () => {
    if (!editingId) return
    try {
      const { model, model_deep } = buildModelPayload()
      const updateData: Record<string, string | boolean | undefined> = {
        name: formData.name,
        description: formData.description,
        model,
        model_deep: model_deep || '',
        base_url: formData.base_url,
        is_default: formData.is_default,
      }
      if (formData.api_key) updateData.api_key = formData.api_key
      await updateLLMConfig(editingId, updateData)
      toast.success('配置更新成功')
      setEditingId(null)
      resetForm()
      loadData()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '更新配置失败')
    }
  }

  const handleDelete = async (config: LLMConfiguration) => {
    if (deleteConfirmId !== config.id) {
      setDeleteConfirmId(config.id)
      return
    }
    setDeleteConfirmId(null)

    try {
      await deleteLLMConfig(config.id, true)
      toast.success(`已删除「${config.name}」`)
      loadData()
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : '删除失败')
    }
  }

  const handleConsolidateDeepseek = async () => {
    try {
      setMerging(true)
      const result = await consolidateDeepseek()
      const n = result.configs_deleted || 0
      toast.success(n > 0 ? `已合并 DeepSeek 配置，删除 ${n} 条重复项` : '没有需要合并的 DeepSeek 重复配置')
      loadData()
    } catch {
      toast.error('DeepSeek 合并失败')
    } finally {
      setMerging(false)
    }
  }

  const handleTest = async (id: number) => {
    try {
      setTestingId(id)
      const result = await testLLMConfig(id)
      toast.success(result.success ? '连接测试成功' : `测试失败: ${result.message}`)
      loadData()
    } catch {
      toast.error('测试失败')
    } finally {
      setTestingId(null)
    }
  }

  const handleSetDefault = async (id: number) => {
    try {
      await setDefaultConfig(id)
      toast.success('已设为默认配置')
      loadData()
    } catch {
      toast.error('设置默认失败')
    }
  }

  const handleToggleActive = async (config: LLMConfiguration) => {
    try {
      await updateLLMConfig(config.id, { is_active: !config.is_active })
      toast.success(config.is_active ? '配置已禁用' : '配置已启用')
      loadData()
    } catch {
      toast.error('操作失败')
    }
  }

  const startEdit = (config: LLMConfiguration) => {
    setEditingId(config.id)
    setDeleteConfirmId(null)
    const isDs = config.provider === 'deepseek'
    const toggles = isDs ? inferDeepseekToggles(config) : { enableFlash: true, enablePro: Boolean(config.model_deep) }
    setFormData({
      name: config.name,
      description: config.description || '',
      model: config.model,
      model_deep: config.model_deep || '',
      base_url: config.base_url,
      api_key: '',
      is_default: config.is_default,
      enableFlash: toggles.enableFlash,
      enablePro: toggles.enablePro,
    })
    const providerIndex = providers.findIndex((p) => p.id === config.provider)
    setSelectedProvider(providerIndex >= 0 ? providerIndex : providers.length - 1)
    setShowAddForm(false)
  }

  const resetForm = () => {
    setFormData({
      name: '',
      description: '',
      model: '',
      model_deep: '',
      base_url: '',
      api_key: '',
      is_default: false,
      enableFlash: true,
      enablePro: true,
    })
    setSelectedProvider(0)
  }

  const getStatusBadge = (config: LLMConfiguration) => {
    if (!config.is_active) return <Badge variant="secondary">禁用</Badge>
    if (config.test_status === 'success') {
      return <Badge variant="default" className="bg-green-600">已验证</Badge>
    }
    if (config.test_status === 'failed') {
      return <Badge variant="destructive">验证失败</Badge>
    }
    return <Badge variant="outline">待验证</Badge>
  }

  const getModelBadges = (config: LLMConfiguration) => {
    if (config.model_deep) {
      return (
        <>
          <Badge variant="outline" className="text-xs border-purple-300 text-purple-600 bg-purple-50">Flash</Badge>
          <Badge variant="outline" className="text-xs border-blue-300 text-blue-600 bg-blue-50">Pro</Badge>
        </>
      )
    }
    const m = config.model.toLowerCase()
    if (m.includes('flash') || m.includes('chat')) {
      return <Badge variant="outline" className="text-xs border-purple-300 text-purple-600 bg-purple-50">快速</Badge>
    }
    if (m.includes('pro') || m.includes('reasoner')) {
      return <Badge variant="outline" className="text-xs border-blue-300 text-blue-600 bg-blue-50">深度</Badge>
    }
    return null
  }

  const getProviderDisplayName = (providerId: string) => {
    return providers.find((p) => p.id === providerId)?.name || providerId
  }

  const formatModels = (config: LLMConfiguration) => {
    if (config.model_deep) {
      return `Flash: ${config.model} · Pro: ${config.model_deep}`
    }
    return `模型: ${config.model}`
  }

  if (loading) {
    return <div className="flex items-center justify-center py-8">加载中...</div>
  }

  const deepseekCount = configs.filter((c) => c.provider === 'deepseek').length

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="text-sm text-muted-foreground">共 {configs.length} 个配置</div>
        <div className="flex gap-2">
          {deepseekCount > 1 && (
            <Button size="sm" variant="outline" disabled={merging} onClick={handleConsolidateDeepseek}>
              <Merge className="h-4 w-4 mr-1" />
              合并 DeepSeek
            </Button>
          )}
          <Button
            size="sm"
            onClick={() => {
              setShowAddForm(!showAddForm)
              setEditingId(null)
              resetForm()
            }}
          >
            <Plus className="h-4 w-4 mr-1" />
            新建配置
          </Button>
        </div>
      </div>

      {(showAddForm || editingId) && (
        <Card className="border-primary/50">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">{editingId ? '编辑配置' : '新建大模型配置'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">选择提供商</label>
              <div className="grid grid-cols-3 gap-2">
                {providers.map((provider, index) => (
                  <button
                    key={provider.id}
                    type="button"
                    onClick={() => handleProviderChange(index)}
                    className={`px-3 py-2 text-sm rounded border transition-colors ${
                      selectedProvider === index
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'bg-background hover:bg-muted border-border'
                    }`}
                  >
                    {provider.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Input
                placeholder="配置名称 *"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              />
              <Input
                placeholder="描述 (可选)"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              />
            </div>

            {isDeepseekDual ? (
              <div className="rounded-lg border border-purple-200 bg-purple-50/50 dark:bg-purple-950/20 p-3 space-y-2">
                <p className="text-sm font-medium text-purple-900 dark:text-purple-200">
                  DeepSeek V4 Flash（统一模型）
                </p>
                <p className="text-xs text-muted-foreground">
                  快速与深度任务均使用 <strong>deepseek-v4-flash</strong>，不再默认调用 V4 Pro。
                </p>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="h-4 w-4"
                    checked={formData.enableFlash}
                    onChange={(e) => setFormData({ ...formData, enableFlash: e.target.checked, enablePro: false })}
                  />
                  V4 Flash — deepseek-v4-flash
                </label>
              </div>
            ) : (
              <Input
                placeholder="模型名称 *"
                value={formData.model}
                onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              />
            )}

            <Input
              placeholder="Base URL *"
              value={formData.base_url}
              onChange={(e) => setFormData({ ...formData, base_url: e.target.value })}
            />
            <Input
              type="password"
              placeholder={editingId ? 'API Key (留空保持不变)' : 'API Key *'}
              value={formData.api_key}
              onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
            />

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={formData.is_default}
                onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
              />
              设为默认配置
            </label>

            <div className="flex gap-2">
              <Button onClick={editingId ? handleUpdate : handleCreate}>
                {editingId ? '保存' : '创建'}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setShowAddForm(false)
                  setEditingId(null)
                  resetForm()
                }}
              >
                取消
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2 max-h-[400px] overflow-y-auto">
        {configs.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <Settings2 className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p>暂无大模型配置</p>
          </div>
        ) : (
          configs.map((config) => (
            <Card key={config.id} className={!config.is_active ? 'opacity-60' : ''}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 space-y-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium">{config.name}</span>
                      {config.is_default && (
                        <Star className="h-4 w-4 text-yellow-500 fill-yellow-500 shrink-0" />
                      )}
                      {getStatusBadge(config)}
                      <Badge variant="outline" className="text-xs">
                        {getProviderDisplayName(config.provider)}
                      </Badge>
                      {getModelBadges(config)}
                    </div>
                    <div className="text-xs text-muted-foreground">{formatModels(config)}</div>
                    <div className="text-xs text-muted-foreground truncate">{config.base_url}</div>
                    <div className="text-xs text-muted-foreground">
                      API Key: {config.api_key_masked} | 使用 {config.usage_count} 次
                      {config.accounts_count > 0 && ` | ${config.accounts_count} 交易员`}
                      {(config.profiles_count || 0) > 0 && ` | ${config.profiles_count} 套利档案`}
                    </div>
                  </div>

                  {!selectionMode && (
                    <div className="flex flex-col items-end gap-2 shrink-0">
                      <div className="flex flex-wrap items-center justify-end gap-1">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleTest(config.id)}
                          disabled={testingId === config.id}
                        >
                          <RefreshCw className={`h-3.5 w-3.5 mr-1 ${testingId === config.id ? 'animate-spin' : ''}`} />
                          测试
                        </Button>
                        {!config.is_default && config.is_active && (
                          <Button size="sm" variant="outline" onClick={() => handleSetDefault(config.id)}>
                            <Star className="h-3.5 w-3.5 mr-1" />
                            设默认
                          </Button>
                        )}
                        <Button size="sm" variant="outline" onClick={() => startEdit(config)}>
                          <Edit2 className="h-3.5 w-3.5 mr-1" />
                          编辑
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => handleToggleActive(config)}>
                          {config.is_active ? <X className="h-3.5 w-3.5 mr-1" /> : <Check className="h-3.5 w-3.5 mr-1" />}
                          {config.is_active ? '禁用' : '启用'}
                        </Button>
                        {deleteConfirmId === config.id ? (
                          <>
                            <Button size="sm" variant="destructive" onClick={() => handleDelete(config)}>
                              确认删除
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setDeleteConfirmId(null)}>
                              取消
                            </Button>
                          </>
                        ) : (
                          <Button size="sm" variant="outline" onClick={() => handleDelete(config)}>
                            <Trash2 className="h-3.5 w-3.5 mr-1 text-destructive" />
                            删除
                          </Button>
                        )}
                      </div>
                      {deleteConfirmId === config.id && (
                        <p className="text-xs text-destructive text-right max-w-[220px]">
                          {formatRefs(config) === '无引用'
                            ? '确认删除此配置？'
                            : `仍被 ${formatRefs(config)} 使用，确认后将自动解除关联`}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      <div className="text-xs text-muted-foreground bg-blue-50 border border-blue-200 rounded p-2 dark:bg-blue-950/30 dark:border-blue-800">
        <p className="font-medium text-blue-900 dark:text-blue-200 mb-1">💡 自动选模型说明</p>
        <p className="text-blue-800 dark:text-blue-300">
          配置页的 Flash / Pro 勾选，只是声明「这个 Key 能用哪些模型」。
          真正调用时由系统按任务自动路由：快任务走 Flash，深度分析走 Pro。
          交易员只需绑定<strong>一条</strong>配置即可，不必再分「分析模型 / 执行模型」两套。
        </p>
      </div>
    </div>
  )
}

export { LLMConfigManager }
