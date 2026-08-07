import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '../ui/dialog'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { ScrollArea } from '../ui/scroll-area'
import { Badge } from '../ui/badge'
import { X, Plus, Search, Check, Loader2 } from 'lucide-react'
import { FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

interface SymbolManageDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentSymbols: string[]
  onSymbolsChange: (symbols: string[]) => void
  exchange: 'binance' | 'hyperliquid'
}

interface AvailableSymbol {
  symbol: string
  price?: number
  volume24h?: number
}

export default function SymbolManageDialog({
  open,
  onOpenChange,
  currentSymbols,
  onSymbolsChange,
  exchange
}: SymbolManageDialogProps) {
  const { t } = useTranslation()
  const [searchQuery, setSearchQuery] = useState('')
  const [availableSymbols, setAvailableSymbols] = useState<AvailableSymbol[]>([])
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([...currentSymbols])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [newSymbol, setNewSymbol] = useState('')

  useEffect(() => {
    if (open) {
      setSaveError(null)
      setAddError(null)
      fetchAvailableSymbols()
      fetchCurrentWatchlist()
    }
  }, [open, exchange])

  const fetchAvailableSymbols = async () => {
    setLoading(true)
    try {
      const marketParam = exchange === 'binance' ? 'binance' : 'hyperliquid'
      const response = await fetch(`/api/market/available-symbols?market=${marketParam}`)
      if (!response.ok) throw new Error('Failed to fetch symbols')

      const data = await response.json()
      setAvailableSymbols(data.symbols || [])
    } catch (error) {
      console.error('Failed to fetch available symbols:', error)
      // 如果API不可用，使用统一的 fallback 交易对列表
      setAvailableSymbols(FALLBACK_TRADING_PAIRS.map(symbol => ({ symbol })))
    } finally {
      setLoading(false)
    }
  }

  const fetchCurrentWatchlist = async () => {
    try {
      const endpoint = exchange === 'binance'
        ? '/api/binance/symbols/watchlist'
        : '/api/hyperliquid/symbols/watchlist'

      const response = await fetch(endpoint)
      if (!response.ok) return

      const data = await response.json()
      let symbols = data.symbols || []

      // For Binance, remove USDT suffix for display
      if (exchange === 'binance') {
        symbols = symbols.map((s: string) => s.replace('USDT', ''))
      }

      setSelectedSymbols(symbols)
    } catch (error) {
      console.error('Failed to fetch current watchlist:', error)
    }
  }

  const handleToggleSymbol = (symbol: string) => {
    setSelectedSymbols(prev =>
      prev.includes(symbol)
        ? prev.filter(s => s !== symbol)
        : [...prev, symbol]
    )
  }

  const [addError, setAddError] = useState<string | null>(null)

  const handleAddCustomSymbol = async () => {
    if (!newSymbol.trim()) return
    setAddError(null)

    const upperSymbol = newSymbol.trim().toUpperCase()

    if (!/^[A-Z0-9]{1,20}$/.test(upperSymbol)) {
      setAddError('Symbol must be uppercase letters/digits only')
      return
    }

    if (selectedSymbols.includes(upperSymbol)) {
      setAddError(`${upperSymbol} is already in the list`)
      setNewSymbol('')
      return
    }

    if (selectedSymbols.length >= 10) {
      setAddError('Maximum 10 symbols allowed')
      return
    }

    setSelectedSymbols(prev => [...prev, upperSymbol])
    if (!availableSymbols.find(s => s.symbol === upperSymbol)) {
      setAvailableSymbols(prev => [...prev, { symbol: upperSymbol }])
    }
    setNewSymbol('')
  }

  const [saveError, setSaveError] = useState<string | null>(null)

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const endpoint = exchange === 'binance'
        ? '/api/binance/symbols/watchlist'
        : '/api/hyperliquid/symbols/watchlist'

      const symbolsToSave = exchange === 'binance'
        ? selectedSymbols.map(s => s.endsWith('USDT') ? s : `${s}USDT`)
        : selectedSymbols

      const response = await fetch(endpoint, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: symbolsToSave })
      })

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail || 'Failed to update watchlist')
      }

      onSymbolsChange(selectedSymbols)
      onOpenChange(false)
    } catch (error: any) {
      console.error('Failed to save watchlist:', error)
      setSaveError(error?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  // 过滤可用交易对
  const filteredSymbols = availableSymbols.filter(symbol => {
    const query = searchQuery.toLowerCase()
    return symbol.symbol.toLowerCase().includes(query)
  })

  // 排序：已选择的在前
  const sortedSymbols = [...filteredSymbols].sort((a, b) => {
    const aSelected = selectedSymbols.includes(a.symbol)
    const bSelected = selectedSymbols.includes(b.symbol)
    if (aSelected && !bSelected) return -1
    if (!aSelected && bSelected) return 1
    return a.symbol.localeCompare(b.symbol)
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh]">
        <DialogHeader>
          <DialogTitle>
            {t('kline.manageWatchlist', 'Manage Watchlist')}
            {exchange === 'binance' ? ' (Binance)' : ' (Hyperliquid)'}
          </DialogTitle>
          <DialogDescription>
            {t('kline.manageWatchlistDesc', 'Add or remove symbols from your watchlist')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 自定义添加交易对 */}
          <div className="flex gap-2">
            <Input
              placeholder={t('kline.enterSymbol', 'Enter symbol (e.g., BTC, ETH)')}
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAddCustomSymbol()}
              className="flex-1"
            />
            <Button
              onClick={handleAddCustomSymbol}
              disabled={!newSymbol.trim()}
              size="sm"
            >
              <Plus className="w-4 h-4 mr-1" />
              {t('common.add', 'Add')}
            </Button>
          </div>

          {addError && (
            <p className="text-xs text-amber-500">{addError}</p>
          )}

          {/* 搜索框 */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              placeholder={t('kline.searchSymbols', 'Search symbols...')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>

          {/* 已选择数量 */}
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              {t('kline.selectedCount', 'Selected: {{count}}', { count: selectedSymbols.length })}
            </span>
            {selectedSymbols.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedSymbols([])}
              >
                <X className="w-4 h-4 mr-1" />
                {t('common.clear', 'Clear')}
              </Button>
            )}
          </div>

          {/* 交易对列表 */}
          <ScrollArea className="h-[400px] border rounded-md">
            <div className="p-4 space-y-1">
              {loading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
              ) : sortedSymbols.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  {searchQuery
                    ? t('kline.noSymbolsFound', 'No symbols found')
                    : t('kline.noSymbolsAvailable', 'No symbols available')
                  }
                </div>
              ) : (
                sortedSymbols.map((symbol) => {
                  const isSelected = selectedSymbols.includes(symbol.symbol)
                  return (
                    <div
                      key={symbol.symbol}
                      className={`flex items-center justify-between p-3 rounded-lg border-2 transition-all cursor-pointer ${
                        isSelected 
                          ? 'bg-blue-50 dark:bg-blue-900/30 border-blue-500 shadow-md' 
                          : 'border-border hover:bg-accent hover:border-blue-300'
                      }`}
                      onClick={() => handleToggleSymbol(symbol.symbol)}
                    >
                      <div className="flex items-center gap-3">
                        <div className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${
                          isSelected 
                            ? 'bg-blue-500 border-blue-500' 
                            : 'border-gray-300 dark:border-gray-600'
                        }`}>
                          {isSelected && (
                            <Check className="w-3.5 h-3.5 text-white font-bold" />
                          )}
                        </div>
                        <div>
                          <div className="font-medium">{symbol.symbol}</div>
                          {symbol.volume24h && (
                            <div className="text-xs text-muted-foreground">
                              Vol: ${(symbol.volume24h / 1_000_000).toFixed(2)}M
                            </div>
                          )}
                        </div>
                      </div>
                      {isSelected && (
                        <Badge variant="default" className="ml-2">
                          {t('common.selected', 'Selected')}
                        </Badge>
                      )}
                    </div>
                  )
                })
              )}
            </div>
          </ScrollArea>
        </div>

        <DialogFooter className="flex-col gap-2 sm:flex-row">
          {saveError && (
            <p className="text-sm text-red-500 mr-auto">{saveError}</p>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button onClick={handleSave} disabled={saving || selectedSymbols.length === 0}>
            {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            {t('common.save', 'Save')} ({selectedSymbols.length})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
