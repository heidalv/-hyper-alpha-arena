/**
 * BinanceView - Binance Trading Mode Main View
 *
 * ARCHITECTURE:
 * - This component is the main container for Binance mode
 * - Uses BinanceDashboard for multi-account summary display
 * - Uses AlphaArenaFeed for real-time trading feed (AI decisions, trades)
 *
 * CURRENT STATUS: Active production component for multi-wallet Binance architecture
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { getAccounts } from '@/lib/api'
import { getBinanceConfig, getBinancePositions } from '@/lib/binanceApi'
import AlphaArenaFeed from '@/components/portfolio/AlphaArenaFeed'
import BinanceDashboard from '@/components/portfolio/BinanceDashboard'

interface BinanceViewProps {
  wsRef?: React.MutableRefObject<WebSocket | null>
  refreshKey?: number
  onPageChange?: (page: string) => void
}

export default function BinanceView({ wsRef, refreshKey = 0, onPageChange }: BinanceViewProps) {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [selectedAccount, setSelectedAccount] = useState<number | 'all'>('all')
  const [hasConfiguredAccounts, setHasConfiguredAccounts] = useState(false)

  // Check if there are configured Binance accounts (only on mount)
  useEffect(() => {
    const checkAccounts = async () => {
      try {
        setLoading(true)
        const accountList = await getAccounts()
        
        // Check each account for Binance configuration
        for (const account of accountList) {
          try {
            const config = await getBinanceConfig(account.id)
            if (config.configured) {
              setHasConfiguredAccounts(true)
              break
            }
          } catch (e) {
            // Ignore errors for individual accounts
          }
        }
      } catch (error) {
        console.error('Failed to check Binance accounts:', error)
      } finally {
        setLoading(false)
      }
    }

    checkAccounts()
  }, []) // ✅ Only run on mount - BinanceDashboard handles its own refresh

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-muted-foreground">{t('dashboard.loadingData', '加载币安数据...')}</div>
      </div>
    )
  }

  if (!hasConfiguredAccounts) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <p className="text-lg text-muted-foreground mb-4">暂无配置的币安账户</p>
          <button
            onClick={() => onPageChange?.('trader-management')}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
          >
            去配置
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="grid gap-6 grid-cols-5 h-full min-h-0">
      {/* Left Panel - Dashboard (Account Summary & Positions) */}
      <div className="col-span-3 flex flex-col gap-4 min-h-0 overflow-y-auto">
        <BinanceDashboard onPageChange={onPageChange} />
      </div>

      {/* Right Panel - Feed (AI Decisions & Trades) */}
      <div className="col-span-2 flex flex-col min-h-0">
        <div className="flex-1 min-h-0 border border-border rounded-lg bg-card shadow-sm px-4 py-3 flex flex-col">
          <AlphaArenaFeed
            wsRef={wsRef}
            selectedAccount={selectedAccount}
            onSelectedAccountChange={setSelectedAccount}
            onPageChange={onPageChange}
          />
        </div>
      </div>
    </div>
  )
}
