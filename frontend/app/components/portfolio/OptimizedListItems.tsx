/**
 * PERFORMANCE OPTIMIZATION: Optimized List Item Components
 *
 * This file contains React.memo optimized components for rendering list items
 * to prevent unnecessary re-renders when parent components update.
 */

import React from 'react'
import { FlipNumber } from './FlipNumber'
import HighlightWrapper from './HighlightWrapper'
import { getModelLogo } from './logoAssets'
import { formatDateTime } from '@/lib/dateTime'
import { useTranslation } from 'react-i18next'

interface TradeItemProps {
  trade: any
  isNew: boolean
  onPnlUpdate?: () => void
  formatDateTime: (date: string) => string
}

/**
 * Optimized Trade Item Component
 *
 * PERFORMANCE: Uses React.memo with custom comparison to only re-render when
 * trade.id or trade.trade_time changes. This prevents re-renders when parent
 * state changes or when other trades in the list are updated.
 */
export const TradeItem = React.memo<TradeItemProps>(({ trade, isNew, formatDateTime }) => {
  const { t } = useTranslation()
  const modelLogo = getModelLogo(trade.account_name || trade.model)

  return (
    <HighlightWrapper key={`${trade.trade_id}-${trade.trade_time}`} isNew={isNew}>
      <div className="border border-border bg-muted/40 rounded px-4 py-3 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wide text-muted-foreground">
          <div className="flex items-center gap-2">
            {modelLogo && (
              <img
                src={modelLogo.src}
                alt={modelLogo.alt}
                className="h-5 w-5 rounded-full object-contain bg-background"
                loading="lazy"
              />
            )}
            <span className="font-semibold text-foreground">{trade.account_name}</span>
          </div>
          <span>{formatDateTime(trade.trade_time)}</span>
        </div>
        <div className="text-sm text-foreground flex flex-wrap items-center gap-2">
          <span className="font-semibold">{trade.account_name}</span>
          <span>{t('feed.completedA', 'completed a')}</span>
          <span className={`px-2 py-1 rounded text-xs font-bold ${
            trade.side === 'BUY'
              ? 'bg-emerald-100 text-emerald-800'
              : trade.side === 'SELL'
              ? 'bg-red-100 text-red-800'
              : trade.side === 'CLOSE'
              ? 'bg-blue-100 text-blue-800'
              : trade.side === 'HOLD'
              ? 'bg-gray-200 text-gray-800'
              : 'bg-orange-100 text-orange-800'
          }`}>
            {trade.side}
          </span>
          <span>{t('feed.tradeOn', 'trade on')}</span>
          <span className="font-semibold">{trade.symbol}</span>
          <span>!</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-muted-foreground">
          <div>
            <span className="block text-[10px] uppercase tracking-wide">{t('feed.price', 'Price')}</span>
            <span className="font-medium text-foreground">
              <FlipNumber value={trade.price} prefix="$" decimals={2} />
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wide">{t('feed.quantity', 'Quantity')}</span>
            <span className="font-medium text-foreground">
              <FlipNumber value={trade.quantity} decimals={4} />
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wide">{t('feed.notional', 'Notional')}</span>
            <span className="font-medium text-foreground">
              <FlipNumber value={trade.notional} prefix="$" decimals={2} />
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wide">{t('feed.commission', 'Commission')}</span>
            <span className="font-medium text-foreground">
              <FlipNumber value={trade.commission} prefix="$" decimals={2} />
            </span>
          </div>
        </div>
      </div>
    </HighlightWrapper>
  )
}, (prevProps, nextProps) => {
  // Custom comparison: only re-render if trade_id or trade_time changes
  return (
    prevProps.trade.trade_id === nextProps.trade.trade_id &&
    prevProps.trade.trade_time === nextProps.trade.trade_time
  )
})

TradeItem.displayName = 'TradeItem'


interface ModelChatItemProps {
  entry: any
  isNew: boolean
  formatDateTime: (date: string) => string
}

/**
 * Optimized Model Chat Item Component
 *
 * PERFORMANCE: Uses React.memo to prevent re-renders when chat entries
 * haven't changed. Compares by entry.id which is unique.
 */
export const ModelChatItem = React.memo<ModelChatItemProps>(({ entry, isNew, formatDateTime }) => {
  const { t } = useTranslation()
  const modelLogo = getModelLogo(entry.account_name || entry.model)
  const decision = entry.decision || {}
  const reasoning = entry.reasoning || ''

  return (
    <HighlightWrapper key={entry.id} isNew={isNew}>
      <div className="border border-border bg-muted/40 rounded px-4 py-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {modelLogo && (
              <img
                src={modelLogo.src}
                alt={modelLogo.alt}
                className="h-6 w-6 rounded-full object-contain bg-background"
                loading="lazy"
              />
            )}
            <span className="font-semibold text-sm">{entry.account_name}</span>
            <span className="text-xs text-muted-foreground">{formatDateTime(entry.timestamp)}</span>
          </div>
          <span className={`px-2 py-1 rounded text-xs font-bold ${
            decision.operation === 'buy'
              ? 'bg-emerald-100 text-emerald-800'
              : decision.operation === 'sell'
              ? 'bg-red-100 text-red-800'
              : 'bg-gray-200 text-gray-800'
          }`}>
            {decision.operation ? decision.operation.toUpperCase() : 'HOLD'}
          </span>
        </div>

        {decision.symbol && (
          <div className="text-sm">
            <span className="font-semibold">{decision.symbol}</span>
            {decision.target_portion_of_balance !== undefined && (
              <span className="text-muted-foreground ml-2">
                {t('feed.targetPortion', 'Target')}: {(decision.target_portion_of_balance * 100).toFixed(1)}%
              </span>
            )}
          </div>
        )}

        {reasoning && (
          <div className="text-xs text-muted-foreground mt-2 pt-2 border-t border-border/50">
            {reasoning}
          </div>
        )}
      </div>
    </HighlightWrapper>
  )
}, (prevProps, nextProps) => {
  // Only re-render if entry.id changes
  return prevProps.entry.id === nextProps.entry.id
})

ModelChatItem.displayName = 'ModelChatItem'
