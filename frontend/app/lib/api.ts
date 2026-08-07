import Cookies from 'js-cookie'

// API configuration
// [2026-08-06] VITE_API_BASE 支持直连后端（绕过 vite 4.5.14 proxy 偶发挂起）
const API_BASE_URL =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'

// Hardcoded user for paper trading (matches backend initialization)
const HARDCODED_USERNAME = 'default'

// Helper function for making API requests
export async function apiRequest(
  endpoint: string, 
  options: RequestInit = {}
): Promise<Response> {
  const url = `${API_BASE_URL}${endpoint}`
  
  const defaultOptions: RequestInit = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  }
  
  const response = await fetch(url, defaultOptions)
  
  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const errorData = await response.json();
      errorMessage = errorData.detail || errorData.message || errorMessage;
    } catch {
      // JSON parse failed, keep the generic message
    }
    throw new Error(errorMessage);
  }
  
  const contentType = response.headers.get('content-type')
  if (!contentType || !contentType.includes('application/json')) {
    throw new Error('Response is not JSON')
  }
  
  return response
}

// Specific API functions
export async function checkRequiredConfigs() {
  const response = await apiRequest('/config/check-required')
  return response.json()
}

// Crypto-specific API functions
export async function getCryptoSymbols() {
  const response = await apiRequest('/crypto/symbols')
  return response.json()
}

export async function getCryptoPrice(symbol: string) {
  const response = await apiRequest(`/crypto/price/${symbol}`)
  return response.json()
}

export async function getCryptoMarketStatus(symbol: string) {
  const response = await apiRequest(`/crypto/status/${symbol}`)
  return response.json()
}

export async function getPopularCryptos() {
  const response = await apiRequest('/crypto/popular')
  return response.json()
}

// AI Decision Log interfaces and functions
export interface AIDecision {
  id: number
  account_id: number
  decision_time: string
  reason: string
  operation: string
  symbol?: string
  prev_portion: number
  target_portion: number
  total_balance: number
  executed: string
  order_id?: number
}

export interface AIDecisionFilters {
  operation?: string
  symbol?: string
  executed?: boolean
  start_date?: string
  end_date?: string
  limit?: number
}

export async function getAIDecisions(accountId: number, filters?: AIDecisionFilters): Promise<AIDecision[]> {
  const params = new URLSearchParams()
  if (filters?.operation) params.append('operation', filters.operation)
  if (filters?.symbol) params.append('symbol', filters.symbol)
  if (filters?.executed !== undefined) params.append('executed', filters.executed.toString())
  if (filters?.start_date) params.append('start_date', filters.start_date)
  if (filters?.end_date) params.append('end_date', filters.end_date)
  if (filters?.limit) params.append('limit', filters.limit.toString())
  
  const queryString = params.toString()
  const endpoint = `/accounts/${accountId}/ai-decisions${queryString ? `?${queryString}` : ''}`
  
  const response = await apiRequest(endpoint)
  return response.json()
}

export async function getAIDecisionById(accountId: number, decisionId: number): Promise<AIDecision> {
  const response = await apiRequest(`/accounts/${accountId}/ai-decisions/${decisionId}`)
  return response.json()
}

export async function getAIDecisionStats(accountId: number, days?: number): Promise<{
  total_decisions: number
  executed_decisions: number
  execution_rate: number
  operations: { [key: string]: number }
  avg_target_portion: number
}> {
  const params = days ? `?days=${days}` : ''
  const response = await apiRequest(`/accounts/${accountId}/ai-decisions/stats${params}`)
  return response.json()
}

// User authentication interfaces
export interface User {
  id: number
  username: string
  email?: string
  is_active: boolean
}

export interface UserAuthResponse {
  user: User
  session_token: string
  expires_at: string
}

// Trading Account management functions
export interface TradingAccount {
  id: number
  user_id: number
  name: string
  model?: string
  base_url?: string
  api_key_set?: boolean
  api_key_masked?: string
  initial_capital: number
  current_cash: number
  frozen_cash: number
  account_type: string
  is_active: boolean
  auto_trading_enabled?: boolean
  wallet_address?: string | null
  has_mainnet_wallet?: boolean
  hyperliquid_enabled?: boolean
  selected_exchange?: string
  llm_config_id?: number | null
  llm_config_name?: string | null
  llm_config_id_deep?: number | null
  llm_config_name_deep?: string | null
}

export interface TraderPersonality {
  id?: number
  display_name?: string
  description?: string
  benchmark_trader?: string
  trading_style?: string
  time_horizon?: string
  risk_appetite?: number
  min_confidence?: number
  loss_tolerance?: number
  win_aggression?: number
  max_position_pct?: number
  preferred_leverage?: number
  max_leverage?: number
  specialty_symbols?: string
  special_skills?: string
  custom_prompt?: string
  preset_key?: string
}

export interface TradingAccountCreate {
  name: string
  model?: string
  base_url?: string
  api_key?: string
  initial_capital?: number
  account_type?: string
  auto_trading_enabled?: boolean
  llm_config_id?: number
  llm_config_id_deep?: number
  selected_exchange?: string
  personality?: TraderPersonality
}

export interface TradingAccountUpdate {
  name?: string
  model?: string
  base_url?: string
  api_key?: string
  auto_trading_enabled?: boolean
  llm_config_id?: number
  llm_config_id_deep?: number
  selected_exchange?: string
  personality?: TraderPersonality
}

export interface ArbitrageProfile {
  id: number | null
  account_id: number
  enabled: boolean
  mode: 'paper' | 'live'
  paper_account_id: number | null
  paper_account_mode?: 'legacy_ai_paper' | 'dedicated_arbitrage_paper'
  arbitrage_paper_account_id?: number | null
  enabled_strategies: string[]
  strategy_overrides: Record<string, any>
  wash_trade_profile: 'conservative' | 'balanced' | 'aggressive' | string
  ai_config_source: string
  linked_llm_config_id: number | null
  strategy_llm_config_id?: number | null
  execution_llm_config_id?: number | null
  last_evolved_at?: number | null
  profile_snapshot?: Record<string, any>
}

export async function getArbitrageProfile(accountId: number): Promise<ArbitrageProfile> {
  const response = await apiRequest(`/accounts/${accountId}/arbitrage-profile`)
  return response.json()
}

export async function saveArbitrageProfile(accountId: number, profile: Partial<ArbitrageProfile>): Promise<{ success: boolean; profile: ArbitrageProfile }> {
  const response = await apiRequest(`/accounts/${accountId}/arbitrage-profile`, {
    method: 'PUT',
    body: JSON.stringify(profile),
  })
  return response.json()
}

export async function aiGenerateArbitrageProfile(accountId: number, payload: { risk_profile?: string; total_equity?: number; goal?: string; target_strategies?: string[] }) {
  const response = await apiRequest(`/accounts/${accountId}/arbitrage-profile/ai-generate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return response.json()
}

export type StrategyTriggerMode = 'realtime' | 'interval' | 'tick_batch'

export interface StrategyConfig {
  trigger_mode: StrategyTriggerMode
  interval_seconds?: number | null
  tick_batch_size?: number | null
  enabled: boolean
  last_trigger_at?: string | null
}

export interface StrategyConfigUpdate {
  trigger_mode: StrategyTriggerMode
  interval_seconds?: number | null
  tick_batch_size?: number | null
  enabled: boolean
}

// Prompt templates & bindings
export interface PromptTemplate {
  id: number
  key: string
  name: string
  description?: string | null
  templateText: string
  systemTemplateText: string
  isSystem: string
  isDeleted: string
  createdBy: string
  updatedBy?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface PromptBinding {
  id: number
  accountId: number
  accountName: string
  accountModel?: string | null
  promptTemplateId: number
  promptKey: string
  promptName: string
  updatedBy?: string | null
  updatedAt?: string | null
}

export interface PromptListResponse {
  templates: PromptTemplate[]
  bindings: PromptBinding[]
}

export interface PromptTemplateUpdateRequest {
  templateText: string
  description?: string
  updatedBy?: string
}

export interface PromptTemplateCreateRequest {
  name: string
  description?: string
  templateText?: string
  createdBy?: string
}

export interface PromptTemplateCopyRequest {
  newName?: string
  createdBy?: string
}

export interface PromptTemplateNameUpdateRequest {
  name: string
  description?: string
  updatedBy?: string
}

export interface PromptBindingUpsertRequest {
  id?: number
  accountId: number
  promptTemplateId: number
  updatedBy?: string
}

export async function getPromptTemplates(): Promise<PromptListResponse> {
  const response = await apiRequest('/prompts')
  return response.json()
}

export async function updatePromptTemplate(
  key: string,
  payload: PromptTemplateUpdateRequest,
): Promise<PromptTemplate> {
  const response = await apiRequest(`/prompts/${encodeURIComponent(key)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
  return response.json()
}

export async function createPromptTemplate(
  payload: PromptTemplateCreateRequest,
): Promise<PromptTemplate> {
  const response = await apiRequest('/prompts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return response.json()
}

export async function copyPromptTemplate(
  templateId: number,
  payload: PromptTemplateCopyRequest,
): Promise<PromptTemplate> {
  const response = await apiRequest(`/prompts/${templateId}/copy`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return response.json()
}

export async function deletePromptTemplate(templateId: number): Promise<void> {
  await apiRequest(`/prompts/${templateId}`, {
    method: 'DELETE',
  })
}

export async function updatePromptTemplateName(
  templateId: number,
  payload: PromptTemplateNameUpdateRequest,
): Promise<PromptTemplate> {
  const response = await apiRequest(`/prompts/${templateId}/name`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
  return response.json()
}

export async function upsertPromptBinding(
  payload: PromptBindingUpsertRequest,
): Promise<PromptBinding> {
  const response = await apiRequest('/prompts/bindings', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return response.json()
}

export async function deletePromptBinding(bindingId: number): Promise<void> {
  await apiRequest(`/prompts/bindings/${bindingId}`, {
    method: 'DELETE',
  })
}

export interface VariablesReferenceResponse {
  content: string
}

export async function getVariablesReference(lang: string = 'en'): Promise<VariablesReferenceResponse> {
  const response = await apiRequest(`/prompts/variables-reference?lang=${lang}`)
  return response.json()
}

export interface PromptPreviewRequest {
  templateText?: string  // Optional: Use this template text directly (for preview before save)
  promptTemplateKey?: string  // Optional: Fallback to database template if templateText not provided
  accountIds: number[]
  symbols?: string[]
}

export interface PromptPreviewItem {
  accountId: number
  accountName: string
  symbols: string[]
  filledPrompt: string
}

export interface PromptPreviewResponse {
  previews: PromptPreviewItem[]
}

export async function previewPrompt(
  payload: PromptPreviewRequest,
): Promise<PromptPreviewResponse> {
  const response = await apiRequest('/prompts/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return response.json()
}


export async function loginUser(username: string, password: string): Promise<UserAuthResponse> {
  const response = await apiRequest('/users/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  return response.json()
}

export async function getUserProfile(sessionToken: string): Promise<User> {
  const response = await apiRequest(`/users/profile?session_token=${sessionToken}`)
  return response.json()
}

// Trading Account management functions (matching backend query parameter style)
export async function listTradingAccounts(sessionToken: string): Promise<TradingAccount[]> {
  const response = await apiRequest(`/accounts/?session_token=${sessionToken}`)
  return response.json()
}

export async function createTradingAccount(account: TradingAccountCreate, sessionToken: string): Promise<TradingAccount> {
  const response = await apiRequest(`/accounts/?session_token=${sessionToken}`, {
    method: 'POST',
    body: JSON.stringify(account),
  })
  return response.json()
}

export async function getAccountStrategy(accountId: number): Promise<StrategyConfig> {
  const response = await apiRequest(`/account/${accountId}/strategy`)
  return response.json()
}

export async function updateAccountStrategy(accountId: number, config: StrategyConfigUpdate): Promise<StrategyConfig> {
  const response = await apiRequest(`/account/${accountId}/strategy`, {
    method: 'PUT',
    body: JSON.stringify(config),
  })
  return response.json()
}

export async function updateTradingAccount(accountId: number, account: TradingAccountUpdate, sessionToken: string): Promise<TradingAccount> {
  const response = await apiRequest(`/accounts/${accountId}?session_token=${sessionToken}`, {
    method: 'PUT',
    body: JSON.stringify(account),
  })
  return response.json()
}

export async function deleteTradingAccount(accountId: number, sessionToken: string): Promise<void> {
  await apiRequest(`/accounts/${accountId}?session_token=${sessionToken}`, {
    method: 'DELETE',
  })
}

// Account functions for paper trading with hardcoded user
// Note: Backend initializes default user on startup, frontend just queries the endpoints
export async function getAccounts(): Promise<TradingAccount[]> {
  const response = await apiRequest('/account/list')
  return response.json()
}

export async function getOverview(): Promise<any> {
  const response = await apiRequest('/account/overview')
  return response.json()
}

export async function createAccount(account: TradingAccountCreate): Promise<TradingAccount> {
  const response = await apiRequest('/account/', {
    method: 'POST',
    body: JSON.stringify({
      name: account.name,
      model: account.model,
      base_url: account.base_url,
      api_key: account.api_key,
      account_type: account.account_type || 'AI',
      initial_capital: account.initial_capital || 10000,
      auto_trading_enabled: account.auto_trading_enabled ?? true,
      llm_config_id: account.llm_config_id,
      llm_config_id_deep: account.llm_config_id_deep,
      selected_exchange: account.selected_exchange,
      personality: account.personality,
    })
  })
  return response.json()
}

export async function updateAccount(accountId: number, account: TradingAccountUpdate): Promise<TradingAccount> {
  const response = await apiRequest(`/account/${accountId}`, {
    method: 'PUT',
    body: JSON.stringify({
      name: account.name,
      model: account.model,
      base_url: account.base_url,
      api_key: account.api_key,
      auto_trading_enabled: account.auto_trading_enabled,
      llm_config_id: account.llm_config_id,
      llm_config_id_deep: account.llm_config_id_deep,
      selected_exchange: account.selected_exchange,
      personality: account.personality,
    })
  })
  return response.json()
}

export async function getPersonalityPresets(): Promise<any[]> {
  const response = await apiRequest('/account/personality-presets')
  return response.json()
}

export async function deleteAccount(accountId: number): Promise<{ success: boolean; message: string; account_id: number; account_name: string }> {
  const response = await apiRequest(`/account/${accountId}`, {
    method: 'DELETE',
  })
  return response.json()
}

export async function testLLMConnection(testData: {
  model?: string;
  base_url?: string;
  api_key?: string;
}): Promise<{ success: boolean; message: string; response?: any }> {
  const response = await apiRequest('/account/test-llm', {
    method: 'POST',
    body: JSON.stringify(testData)
  })
  return response.json()
}

// Alpha Arena aggregated feeds
export interface ArenaAccountMeta {
  account_id: number
  name: string
  model?: string | null
}

export interface ArenaTrade {
  trade_id: number
  order_id?: number | null
  order_no?: string | null
  account_id: number
  account_name: string
  model?: string | null
  side: string
  direction: string
  symbol: string
  market: string
  price: number
  quantity: number
  notional: number
  commission: number
  trade_time?: string | null
  wallet_address?: string | null
  signal_trigger_id?: number | null
  prompt_template_id?: number | null
  prompt_template_name?: string | null
}

export interface ArenaTradesResponse {
  generated_at: string
  accounts: ArenaAccountMeta[]
  trades: ArenaTrade[]
}

export async function getArenaTrades(params?: { limit?: number; account_id?: number; trading_mode?: string; wallet_address?: string, symbol?: string }): Promise<ArenaTradesResponse> {
  const search = new URLSearchParams()
  if (params?.limit) search.append('limit', params.limit.toString())
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.wallet_address) search.append('wallet_address', params.wallet_address)
  if (params?.symbol) search.append('symbol', params.symbol)
  const query = search.toString()
  const response = await apiRequest(`/arena/trades${query ? `?${query}` : ''}`)
  return response.json()
}

export interface UpdatePnlEnvironmentResult {
  fills_count: number
  unique_orders: number
  trades_updated: number
  decisions_updated: number
  skipped: number
}

export interface UpdatePnlResponse {
  success: boolean
  message?: string
  environments: Record<string, UpdatePnlEnvironmentResult>
  errors: string[]
}

export async function updateArenaPnl(): Promise<UpdatePnlResponse> {
  const response = await apiRequest('/arena/update-pnl', { method: 'POST' })
  return response.json()
}

export interface PnlSyncStatus {
  needs_sync: boolean
  unsync_count: number
}

export async function checkPnlSyncStatus(tradingMode?: string): Promise<PnlSyncStatus> {
  const params = new URLSearchParams()
  if (tradingMode) params.append('trading_mode', tradingMode)
  const query = params.toString()
  const response = await apiRequest(`/arena/check-pnl-status${query ? `?${query}` : ''}`)
  return response.json()
}

export interface ArenaModelChatEntry {
  id: number
  account_id: number
  account_name: string
  model?: string | null
  operation: string
  symbol?: string | null
  reason: string
  executed: boolean
  prev_portion: number
  target_portion: number
  total_balance: number
  order_id?: number | null
  decision_time?: string | null
  trigger_mode?: StrategyTriggerMode | null
  strategy_enabled?: boolean
  last_trigger_at?: string | null
  trigger_latency_seconds?: number | null
  prompt_snapshot?: string | null
  reasoning_snapshot?: string | null
  decision_snapshot?: string | null
  wallet_address?: string | null
  signal_trigger_id?: number | null
  prompt_template_id?: number | null
  prompt_template_name?: string | null
  // 三周期独立分析
  short_bias?: string | null
  short_confidence?: number | null
  mid_bias?: string | null
  mid_confidence?: number | null
  long_bias?: string | null
  long_confidence?: number | null
}

export interface ArenaModelChatResponse {
  generated_at: string
  entries: ArenaModelChatEntry[]
}

export async function getArenaModelChat(params?: { limit?: number; account_id?: number; trading_mode?: string; wallet_address?: string; before_time?: string, symbol?: string }): Promise<ArenaModelChatResponse> {
  const search = new URLSearchParams()
  if (params?.limit) search.append('limit', params.limit.toString())
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.wallet_address) search.append('wallet_address', params.wallet_address)
  if (params?.before_time) search.append('before_time', params.before_time)
  if (params?.symbol) search.append('symbol', params.symbol)
  const query = search.toString()
  const response = await apiRequest(`/arena/model-chat${query ? `?${query}` : ''}`)
  return response.json()
}

export interface ModelChatSnapshots {
  id: number
  prompt_snapshot?: string | null
  reasoning_snapshot?: string | null
  decision_snapshot?: string | null
  // 三周期独立分析
  short_bias?: string | null
  short_confidence?: number | null
  mid_bias?: string | null
  mid_confidence?: number | null
  long_bias?: string | null
  long_confidence?: number | null
  error?: string
}

export async function getModelChatSnapshots(decisionId: number): Promise<ModelChatSnapshots> {
  const response = await apiRequest(`/arena/model-chat/${decisionId}/snapshots`)
  return response.json()
}

export interface ArenaPositionItem {
  id: number
  symbol: string
  name: string
  market: string
  side: string
  quantity: number
  avg_cost: number
  current_price: number
  notional: number
  current_value: number
  unrealized_pnl: number
  leverage?: number | null
  margin_used?: number | null
  return_on_equity?: number | null
  percentage?: number | null
  margin_mode?: string | null
  liquidation_px?: number | null
  max_leverage?: number | null
  leverage_type?: string | null
}

export interface ArenaPositionsAccount {
  account_id: number
  account_name: string
  model?: string | null
  environment?: string | null
  wallet_address?: string | null
  total_unrealized_pnl: number
  available_cash: number
  used_margin?: number | null
  positions: ArenaPositionItem[]
  total_assets: number
  initial_capital: number
  total_return?: number | null
  margin_usage_percent?: number | null
  margin_mode?: string | null
}

export interface ArenaPositionsResponse {
  generated_at: string
  accounts: ArenaPositionsAccount[]
}

export async function getArenaPositions(params?: { account_id?: number; trading_mode?: string }): Promise<ArenaPositionsResponse> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  const response = await apiRequest(`/arena/positions${query ? `?${query}` : ''}`)
  const data = await response.json()

  const accounts = Array.isArray(data.accounts)
    ? data.accounts.map((account: any) => ({
        account_id: Number(account.account_id),
        account_name: account.account_name ?? '',
        model: account.model ?? null,
        environment: account.environment ?? null,
        wallet_address: account.wallet_address ?? null,
        total_unrealized_pnl: Number(account.total_unrealized_pnl ?? 0),
        available_cash: Number(account.available_cash ?? 0),
        positions_value: Number(account.positions_value ?? account.used_margin ?? 0),
        used_margin: account.used_margin !== undefined ? Number(account.used_margin) : null,
        total_assets: Number(account.total_assets ?? 0),
        initial_capital: Number(account.initial_capital ?? 0),
        total_return:
          account.total_return !== undefined && account.total_return !== null
            ? Number(account.total_return)
            : null,
        margin_usage_percent:
          account.margin_usage_percent !== undefined && account.margin_usage_percent !== null
            ? Number(account.margin_usage_percent)
            : null,
        margin_mode: account.margin_mode ?? null,
        positions: Array.isArray(account.positions)
          ? account.positions.map((pos: any, idx: number) => ({
              id: pos.id ?? idx,
              symbol: pos.symbol ?? '',
              name: pos.name ?? '',
              market: pos.market ?? '',
              side: pos.side ?? '',
              quantity: Number(pos.quantity ?? 0),
              avg_cost: Number(pos.avg_cost ?? 0),
              current_price: Number(pos.current_price ?? 0),
              notional: Number(pos.notional ?? 0),
              current_value: Number(pos.current_value ?? 0),
              unrealized_pnl: Number(pos.unrealized_pnl ?? 0),
              leverage:
                pos.leverage !== undefined && pos.leverage !== null
                  ? Number(pos.leverage)
                  : null,
              margin_used:
                pos.margin_used !== undefined && pos.margin_used !== null
                  ? Number(pos.margin_used)
                  : null,
              return_on_equity:
                pos.return_on_equity !== undefined && pos.return_on_equity !== null
                  ? Number(pos.return_on_equity)
                  : null,
              percentage:
                pos.percentage !== undefined && pos.percentage !== null
                  ? Number(pos.percentage)
                  : null,
              margin_mode: pos.margin_mode ?? null,
              liquidation_px:
                pos.liquidation_px !== undefined && pos.liquidation_px !== null
                  ? Number(pos.liquidation_px)
                  : null,
              max_leverage:
                pos.max_leverage !== undefined && pos.max_leverage !== null
                  ? Number(pos.max_leverage)
                  : null,
              leverage_type: pos.leverage_type ?? null,
            }))
          : [],
      }))
    : []

  return {
    generated_at: data.generated_at ?? new Date().toISOString(),
    accounts,
  }
}

export interface ArenaAnalyticsAccount {
  account_id: number
  account_name: string
  model?: string | null
  initial_capital: number
  current_cash: number
  positions_value: number
  total_assets: number
  total_pnl: number
  total_return_pct?: number | null
  total_fees: number
  trade_count: number
  total_volume: number
  first_trade_time?: string | null
  last_trade_time?: string | null
  biggest_gain: number
  biggest_loss: number
  win_rate?: number | null
  loss_rate?: number | null
  sharpe_ratio?: number | null
  balance_volatility: number
  decision_count: number
  executed_decisions: number
  decision_execution_rate?: number | null
  avg_target_portion?: number | null
  avg_decision_interval_minutes?: number | null
}

export interface ArenaAnalyticsSummary {
  total_assets: number
  total_pnl: number
  total_return_pct?: number | null
  total_fees: number
  total_volume: number
  average_sharpe_ratio?: number | null
}

export interface ArenaAnalyticsResponse {
  generated_at: string
  accounts: ArenaAnalyticsAccount[]
  summary: ArenaAnalyticsSummary
}

export async function getArenaAnalytics(params?: { account_id?: number }): Promise<ArenaAnalyticsResponse> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  const query = search.toString()
  const response = await apiRequest(`/arena/analytics${query ? `?${query}` : ''}`)
  return response.json()
}

// Hyperliquid symbol configuration
export interface HyperliquidSymbolMeta {
  symbol: string
  name?: string
  type?: string
}

export interface HyperliquidAvailableSymbolsResponse {
  symbols: HyperliquidSymbolMeta[]
  updated_at?: string
  max_symbols: number
}

export interface HyperliquidWatchlistResponse {
  symbols: string[]
  max_symbols: number
}

export async function getHyperliquidAvailableSymbols(): Promise<HyperliquidAvailableSymbolsResponse> {
  const response = await apiRequest('/hyperliquid/symbols/available')
  return response.json()
}

export async function getHyperliquidWatchlist(): Promise<HyperliquidWatchlistResponse> {
  const response = await apiRequest('/hyperliquid/symbols/watchlist')
  return response.json()
}

export async function updateHyperliquidWatchlist(symbols: string[]): Promise<HyperliquidWatchlistResponse> {
  const response = await apiRequest('/hyperliquid/symbols/watchlist', {
    method: 'PUT',
    body: JSON.stringify({ symbols }),
  })
  return response.json()
}

// Binance watchlist APIs
export async function getBinanceWatchlist(): Promise<HyperliquidWatchlistResponse> {
  const response = await apiRequest('/binance/symbols/watchlist')
  return response.json()
}

export async function updateBinanceWatchlist(symbols: string[]): Promise<HyperliquidWatchlistResponse> {
  const response = await apiRequest('/binance/symbols/watchlist', {
    method: 'PUT',
    body: JSON.stringify(symbols),
  })
  return response.json()
}

// Legacy aliases for backward compatibility
export type AIAccount = TradingAccount
export type AIAccountCreate = TradingAccountCreate

// Updated legacy functions to use default mode for simulation
export const listAIAccounts = () => getAccounts()
export const createAIAccount = (account: any) => {
  console.warn("createAIAccount is deprecated. Use default mode or new trading account APIs.")
  return Promise.resolve({} as TradingAccount)
}
export const updateAIAccount = (id: number, account: any) => {
  console.warn("updateAIAccount is deprecated. Use default mode or new trading account APIs.")
  return Promise.resolve({} as TradingAccount)
}
export const deleteAIAccount = (id: number) => {
  console.warn("deleteAIAccount is deprecated. Use default mode or new trading account APIs.")
  return Promise.resolve()
}

// Membership interfaces
export interface MembershipInfo {
  status: string
  planKey: string
  planId?: string
  subscriptionId?: string
  environment: string
  currentPeriodStart?: string
  currentPeriodEnd?: string
  nextBillingTime?: string
  lastPaymentTime?: string
  updatedAt?: string
}

export interface MembershipEvent {
  id: number
  eventType: string
  status: string
  createdAt: string
  environment: string
}

export interface MembershipResponse {
  membership: MembershipInfo | null
  events?: MembershipEvent[]
}

// Get membership information from external membership service
// IMPORTANT: This function supports both same-domain and cross-domain access
// - Same-domain: Uses cookies automatically
// - Cross-domain (localhost/custom domains): Uses Authorization header with arena_token
// This ensures paid users can access membership features regardless of deployment domain
export async function getMembershipInfo(): Promise<MembershipResponse> {
  // 🔓 LOCAL UNLOCK: Always return premium membership for local use
  console.log('[Membership] 🔓 LOCAL MODE: Returning premium membership')
  return {
    membership: {
      status: 'ACTIVE',
      planKey: 'LOCAL_PREMIUM',
      environment: 'local',
      currentPeriodStart: new Date().toISOString(),
      currentPeriodEnd: '2099-12-31T23:59:59Z',
      updatedAt: new Date().toISOString()
    }
  }

  /* Original implementation - commented out for local unlock
  try {
    // Get arena_token for cross-domain authentication
    // This is the same Casdoor access token used for login
    const token = Cookies.get('arena_token')

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }

    // Add Authorization header if token exists (critical for localhost/custom domain deployments)
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    const response = await fetch('https://www.akooi.com/api/membership/me', {
      method: 'GET',
      credentials: 'include',  // Still include credentials for same-domain cookie support
      headers,
    })

    if (!response.ok) {
      if (response.status === 401) {
        // User not authenticated or no membership
        // This can happen if:
        // 1. User is not logged in to www.akooi.com
        // 2. Cross-site cookies are blocked (localhost access with old cookies)
        // 3. Token has expired
        console.warn('[Membership] 401 Unauthorized - Please re-login at https://www.akooi.com to refresh your session')
        return { membership: null }
      }
      throw new Error(`Failed to fetch membership info: ${response.status}`)
    }

    const data = await response.json()
    return data
  } catch (error) {
    console.error('Error fetching membership info:', error)
    // Return null membership on error to gracefully degrade
    return { membership: null }
  }
  */
}

// Hyperliquid Builder Fee Authorization APIs
export interface BuilderAuthorizationStatus {
  authorized: boolean
  max_fee: number
  required_fee: number
  builder_address: string
}

export interface UnauthorizedAccount {
  account_id: number
  account_name: string
  wallet_address: string
  max_fee: number
  required_fee: number
  error_message?: string
}

export interface CheckMainnetAccountsResponse {
  unauthorized_accounts: UnauthorizedAccount[]
}

export interface ApproveBuilderResponse {
  success: boolean
  message: string
  builder_address: string
  approved_fee: string
  result?: unknown
}

export interface DisableTradingResponse {
  success: boolean
  message: string
  account_id: number
  account_name: string
}

export async function checkBuilderAuthorization(walletAddress: string): Promise<BuilderAuthorizationStatus> {
  const response = await apiRequest(`/account/hyperliquid/check-builder-authorization?wallet_address=${walletAddress}`)
  return response.json()
}

export async function checkMainnetAccounts(): Promise<CheckMainnetAccountsResponse> {
  const response = await apiRequest('/account/hyperliquid/check-mainnet-accounts')
  return response.json()
}

export async function approveBuilder(accountId: number): Promise<ApproveBuilderResponse> {
  const response = await apiRequest(`/account/hyperliquid/approve-builder?account_id=${accountId}`, {
    method: 'POST'
  })
  return response.json()
}

export async function disableTrading(accountId: number): Promise<DisableTradingResponse> {
  const response = await apiRequest(`/account/${accountId}/disable-trading`, {
    method: 'POST'
  })
  return response.json()
}

// ============================================================================
// Performance & Analytics APIs
// ============================================================================

export interface PerformanceMetrics {
  period_start: string
  period_end: string
  total_trades: number
  winning_trades: number
  losing_trades: number
  total_pnl: number
  total_pnl_pct: number
  avg_win: number
  avg_loss: number
  avg_trade_pnl: number
  best_trade_pct: number
  worst_trade_pct: number
  max_drawdown: number
  max_drawdown_pct: number
  current_drawdown: number
  volatility: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  var_95: number
  win_rate: number
  profit_factor: number
  expectancy: number
  recovery_factor: number
  risk_reward_ratio: number
  avg_holding_period: number
  longest_holding_period: number
  trades_per_day: number
  consecutive_wins: number
  consecutive_losses: number
  final_equity: number
  initial_equity: number
  max_equity: number
  min_equity: number
  by_symbol: Record<string, {
    trades: number
    wins: number
    pnl: number
    pnl_pct: number
    win_rate: number
  }>
}

export async function getPerformanceMetrics(params?: {
  account_id?: number
  trading_mode?: string
  start_date?: string
  end_date?: string
}): Promise<PerformanceMetrics> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  const query = search.toString()
  const response = await apiRequest(`/analytics/performance${query ? `?${query}` : ''}`)
  return response.json()
}

export interface PerformanceSummary {
  status: string
  period: { start: string; end: string }
  returns: {
    total_pnl: number
    total_pnl_pct: number
    avg_trade_pnl: number
    best_trade: number
    worst_trade: number
  }
  risk: {
    max_drawdown_pct: number
    current_drawdown: number
    volatility: number
    sharpe_ratio: number
    sortino_ratio: number
    var_95: number
  }
  efficiency: {
    win_rate: number
    profit_factor: number
    expectancy: number
    avg_holding_hours: number
  }
  consistency: {
    consecutive_wins: number
    consecutive_losses: number
    trades_per_day: number
  }
}

export async function getPerformanceSummary(params?: {
  account_id?: number
  trading_mode?: string
}): Promise<PerformanceSummary> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  const response = await apiRequest(`/analytics/performance/summary${query ? `?${query}` : ''}`)
  return response.json()
}

// ============================================================================
// Trade Review APIs
// ============================================================================

export interface TradeReviewDimension {
  dimension: string
  score: number
  weight: number
  weighted_score: number
  comments: string[]
  issues: string[]
  suggestions: string[]
}

export interface TradeReview {
  trade_id: string
  symbol: string
  side: string
  entry_price: number
  exit_price: number
  quantity: number
  entry_time: string
  exit_time: string
  pnl: number
  pnl_pct: number
  status: string
  overall_score: number
  max_score: number
  dimensions: Record<string, TradeReviewDimension>
  conclusion: string
  lessons_learned: string[]
  improvement_actions: string[]
  market_regime_entry?: string
  market_regime_exit?: string
  ai_confidence?: number
  ai_reasoning?: string
  factor_weights?: Record<string, number>
  reviewed_at?: string
}

export interface ReviewSummary {
  total_reviews: number
  avg_overall_score: number
  score_distribution: {
    excellent: number
    good: number
    acceptable: number
    poor: number
  }
  total_pnl: number
  avg_pnl: number
  win_rate: number
  dimension_averages: Record<string, number>
}

export async function getTradeReviews(params?: {
  account_id?: number
  trading_mode?: string
  symbol?: string
  status?: string
  limit?: number
}): Promise<{ reviews: TradeReview[]; summary: ReviewSummary }> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.symbol) search.append('symbol', params.symbol)
  if (params?.status) search.append('status', params.status)
  if (params?.limit) search.append('limit', params.limit.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/reviews${query ? `?${query}` : ''}`)
  return response.json()
}

export async function getTradeReviewById(tradeId: string): Promise<TradeReview> {
  const response = await apiRequest(`/analytics/reviews/${tradeId}`)
  return response.json()
}

export async function triggerTradeReview(tradeId: string): Promise<{ success: boolean; review: TradeReview }> {
  const response = await apiRequest(`/analytics/reviews/${tradeId}/trigger`, {
    method: 'POST'
  })
  return response.json()
}

// ============================================================================
// Learning & Insights APIs
// ============================================================================

export interface LearningInsight {
  insight_type: string
  title: string
  description: string
  evidence: string[]
  recommendation: string
  confidence: number
  supporting_trades: number
  created_at: string
  applicable: boolean
}

export interface LearningRecommendation {
  category: string
  priority: string
  action: string
  rationale: string
  expected_impact: string
  implementation: string
}

export interface LearningReport {
  generated_at: string
  insights_count: number
  recommendations_count: number
  top_insights: Array<{
    type: string
    title: string
    confidence: number
    supporting_trades: number
    recommendation: string
  }>
  actionable_recommendations: Array<{
    category: string
    priority: string
    action: string
    rationale: string
    implementation: string
  }>
  factor_performance_summary: Record<string, {
    sample_count: number
    avg_positive: number
    avg_negative: number
  }>
  regime_performance_summary: Record<string, {
    trades: number
    win_rate: number
    avg_pnl: number
  }>
}

export async function getLearningInsights(params?: {
  account_id?: number
  trading_mode?: string
  insight_type?: string
  min_confidence?: number
}): Promise<{ insights: LearningInsight[] }> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.insight_type) search.append('insight_type', params.insight_type)
  if (params?.min_confidence) search.append('min_confidence', params.min_confidence.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/learning/insights${query ? `?${query}` : ''}`)
  return response.json()
}

export async function getLearningRecommendations(params?: {
  account_id?: number
  trading_mode?: string
  priority?: string
}): Promise<{ recommendations: LearningRecommendation[] }> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.priority) search.append('priority', params.priority)
  const query = search.toString()
  const response = await apiRequest(`/analytics/learning/recommendations${query ? `?${query}` : ''}`)
  return response.json()
}

export async function getLearningReport(params?: {
  account_id?: number
  trading_mode?: string
}): Promise<LearningReport> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  const response = await apiRequest(`/analytics/learning/report${query ? `?${query}` : ''}`)
  return response.json()
}

export async function triggerLearningAnalysis(params?: {
  account_id?: number
  trading_mode?: string
}): Promise<{ success: boolean; insights_count: number; recommendations_count: number }> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  const response = await apiRequest(`/analytics/learning/trigger${query ? `?${query}` : ''}`, {
    method: 'POST'
  })
  return response.json()
}

// ============================================================================
// Factor Analysis APIs
// ============================================================================

export interface FactorValue {
  name: string
  value: number
  normalized: number
  category: string
}

export interface AdaptiveWeights {
  weights: Record<string, number>
  regime: string
  confidence: number
  transition_smoothed: boolean
}

export interface FactorContext {
  factor_values: Record<string, number>
  adaptive_weights: Record<string, number>
  market_regime: string
  regime_confidence: number
  selected_factors: string[]
}

export interface ExecutionParameters {
  position_size_pct: number
  stop_loss_pct: number
  take_profit_pct: number
  trailing_stop: boolean
  time_stop: boolean
  leverage: number
  risk_reward_ratio: number
}

export interface AdaptiveParameters {
  market_regime: string
  regime_confidence: number
  factor_weights: Record<string, number>
  factor_summary: string
  execution_parameters: ExecutionParameters
  execution_summary: string
}

export async function getAdaptiveParameters(symbol: string, params?: {
  account_id?: number
  trading_mode?: string
}): Promise<AdaptiveParameters> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  const response = await apiRequest(`/analytics/factors/${symbol}/adaptive${query ? `?${query}` : ''}`)
  return response.json()
}

export async function getFactorValues(symbol: string, params?: {
  account_id?: number
  trading_mode?: string
}): Promise<{ factors: FactorValue[] }> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  const response = await apiRequest(`/analytics/factors/${symbol}${query ? `?${query}` : ''}`)
  return response.json()
}

export async function getAllAdaptiveParameters(params?: {
  account_id?: number
  trading_mode?: string
  symbols?: string[]
}): Promise<Record<string, AdaptiveParameters>> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  if (params?.symbols) search.append('symbols', params.symbols.join(','))
  const query = search.toString()
  const response = await apiRequest(`/analytics/factors/adaptive${query ? `?${query}` : ''}`)
  return response.json()
}

// ============================================================================
// SL/TP Calculator APIs
// ============================================================================

export interface SLTPStrategy {
  use_trailing_stop: boolean
  use_time_stop: boolean
  use_volatility_adjustment: boolean
  trailing_activation_pct: number
  trailing_distance_pct: number
  tp1_distance_pct: number
  tp2_distance_pct: number
  tp3_distance_pct: number
  tp1_close_pct: number
  tp2_close_pct: number
  tp3_close_pct: number
}

export interface SLTPSummary {
  initial_stop: {
    price: number
    distance_pct: number
    reason: string
  }
  trailing_stop: {
    price: number | null
    type: string | null
  }
  breakeven_stop: {
    price: number | null
  }
  final_stop: number
  take_profit_levels: Record<string, { price: number; close_pct: number }>
  risk_reward_ratio: {
    tp1_rr: number
    tp2_rr: number
    tp3_rr: number
  }
}

export async function calculateSLTP(
  symbol: string,
  entryPrice: number,
  side: string,
  atr: number,
  params?: {
    account_id?: number
    trading_mode?: string
    strategy?: SLTPStrategy
  }
): Promise<SLTPSummary> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  
  const response = await apiRequest(`/analytics/sltp/${symbol}${query ? `?${query}` : ''}`, {
    method: 'POST',
    body: JSON.stringify({
      entry_price: entryPrice,
      side,
      atr,
      strategy: params?.strategy
    })
  })
  return response.json()
}

export async function getPositionSize(
  symbol: string,
  entryPrice: number,
  stopLoss: number,
  side: string,
  params?: {
    account_id?: number
    trading_mode?: string
    win_rate?: number
    avg_win?: number
    avg_loss?: number
    volatility?: number
    confidence?: number
  }
): Promise<{
  size: number
  size_pct: number
  risk_amount: number
  risk_pct: number
  leverage: number
  kelly_pct: number
  adjustment_reasons: string[]
  warnings: string[]
}> {
  const search = new URLSearchParams()
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  if (params?.trading_mode) search.append('trading_mode', params.trading_mode)
  const query = search.toString()
  
  const response = await apiRequest(`/analytics/position-size/${symbol}${query ? `?${query}` : ''}`, {
    method: 'POST',
    body: JSON.stringify({
      entry_price: entryPrice,
      stop_loss: stopLoss,
      side,
      win_rate: params?.win_rate,
      avg_win: params?.avg_win,
      avg_loss: params?.avg_loss,
      volatility: params?.volatility,
      confidence: params?.confidence
    })
  })
  return response.json()
}

// ============================================================================
// Report Generation APIs
// ============================================================================

export interface ReportConfig {
  title: string
  period_days: number
  include_charts: boolean
  include_details: boolean
  format: 'markdown' | 'html' | 'json'
}

export async function generatePerformanceReport(config: ReportConfig & {
  account_id?: number
  trading_mode?: string
}): Promise<string> {
  const response = await apiRequest('/analytics/reports/performance', {
    method: 'POST',
    body: JSON.stringify(config)
  })
  return response.text()
}

export async function generateReviewReport(config: ReportConfig & {
  account_id?: number
  trading_mode?: string
}): Promise<string> {
  const response = await apiRequest('/analytics/reports/review', {
    method: 'POST',
    body: JSON.stringify(config)
  })
  return response.text()
}

export async function generateLearningReport(config: ReportConfig & {
  account_id?: number
  trading_mode?: string
}): Promise<string> {
  const response = await apiRequest('/analytics/reports/learning', {
    method: 'POST',
    body: JSON.stringify(config)
  })
  return response.text()
}

export async function generateComprehensiveReport(config: ReportConfig & {
  account_id?: number
  trading_mode?: string
}): Promise<string> {
  const response = await apiRequest('/analytics/reports/comprehensive', {
    method: 'POST',
    body: JSON.stringify(config)
  })
  return response.text()
}

// ============================================================================
// Analytics Dimension Breakdown APIs (by-strategy / by-account / by-symbol / by-operation / by-trigger-type / summary)
// ============================================================================

export interface AnalyticsMetricItem {
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number
  net_pnl: number
  total_pnl: number
  avg_pnl: number
  best_trade: number
  worst_trade: number
  avg_holding_hours: number | null
  profit_factor: number
  expectancy: number
  sharpe_ratio: number | null
  max_drawdown_pct: number
}

export interface TriggerTypeBreakdown {
  count: number
  net_pnl: number
}

export interface AnalyticsSummaryResponse {
  period: { start: string | null; end: string | null }
  overview: AnalyticsMetricItem
  data_completeness: {
    total_decisions: number
    with_strategy: number
    with_signal: number
    with_pnl: number
  }
  by_trigger_type: Record<string, { count: number; metrics: AnalyticsMetricItem }>
}

export async function getAnalyticsSummary(params?: {
  start_date?: string
  end_date?: string
  environment?: string
  account_id?: number
}): Promise<AnalyticsSummaryResponse> {
  const search = new URLSearchParams()
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  if (params?.environment) search.append('environment', params.environment)
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/summary${query ? `?${query}` : ''}`)
  return response.json()
}

export interface AnalyticsByStrategyItem {
  strategy_id: number
  strategy_name: string
  metrics: AnalyticsMetricItem
  by_trigger_type: { signal: TriggerTypeBreakdown; scheduled: TriggerTypeBreakdown }
}

export interface AnalyticsByStrategyResponse {
  items: AnalyticsByStrategyItem[]
  unattributed: { count: number; metrics: AnalyticsMetricItem | null }
}

export async function getAnalyticsByStrategy(params?: {
  start_date?: string
  end_date?: string
  environment?: string
  account_id?: number
}): Promise<AnalyticsByStrategyResponse> {
  const search = new URLSearchParams()
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  if (params?.environment) search.append('environment', params.environment)
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/by-strategy${query ? `?${query}` : ''}`)
  return response.json()
}

export interface AnalyticsByAccountItem {
  account_id: number
  account_name: string
  model: string | null
  environment: string | null
  metrics: AnalyticsMetricItem
  by_trigger_type: { signal: TriggerTypeBreakdown; scheduled: TriggerTypeBreakdown }
}

export interface AnalyticsByAccountResponse {
  items: AnalyticsByAccountItem[]
  unattributed: { count: number; metrics: AnalyticsMetricItem | null }
}

export async function getAnalyticsByAccount(params?: {
  start_date?: string
  end_date?: string
  environment?: string
}): Promise<AnalyticsByAccountResponse> {
  const search = new URLSearchParams()
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  if (params?.environment) search.append('environment', params.environment)
  const query = search.toString()
  const response = await apiRequest(`/analytics/by-account${query ? `?${query}` : ''}`)
  return response.json()
}

export interface AnalyticsBySymbolItem {
  symbol: string
  metrics: AnalyticsMetricItem
  by_trigger_type: { signal: TriggerTypeBreakdown; scheduled: TriggerTypeBreakdown }
}

export interface AnalyticsBySymbolResponse {
  items: AnalyticsBySymbolItem[]
  unattributed: { count: number; metrics: AnalyticsMetricItem | null }
}

export async function getAnalyticsBySymbol(params?: {
  start_date?: string
  end_date?: string
  environment?: string
  account_id?: number
}): Promise<AnalyticsBySymbolResponse> {
  const search = new URLSearchParams()
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  if (params?.environment) search.append('environment', params.environment)
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/by-symbol${query ? `?${query}` : ''}`)
  return response.json()
}

export interface AnalyticsByOperationItem {
  operation: string
  metrics: AnalyticsMetricItem
  by_trigger_type: { signal: TriggerTypeBreakdown; scheduled: TriggerTypeBreakdown }
}

export interface AnalyticsByOperationResponse {
  items: AnalyticsByOperationItem[]
}

export async function getAnalyticsByOperation(params?: {
  start_date?: string
  end_date?: string
  environment?: string
  account_id?: number
}): Promise<AnalyticsByOperationResponse> {
  const search = new URLSearchParams()
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  if (params?.environment) search.append('environment', params.environment)
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/by-operation${query ? `?${query}` : ''}`)
  return response.json()
}

export interface AnalyticsByTriggerTypeItem {
  trigger_type: string
  metrics: AnalyticsMetricItem
  by_operation: Record<string, { count: number; net_pnl: number }>
}

export interface AnalyticsByTriggerTypeResponse {
  items: AnalyticsByTriggerTypeItem[]
}

export async function getAnalyticsByTriggerType(params?: {
  start_date?: string
  end_date?: string
  environment?: string
  account_id?: number
}): Promise<AnalyticsByTriggerTypeResponse> {
  const search = new URLSearchParams()
  if (params?.start_date) search.append('start_date', params.start_date)
  if (params?.end_date) search.append('end_date', params.end_date)
  if (params?.environment) search.append('environment', params.environment)
  if (params?.account_id) search.append('account_id', params.account_id.toString())
  const query = search.toString()
  const response = await apiRequest(`/analytics/by-trigger-type${query ? `?${query}` : ''}`)
  return response.json()
}
