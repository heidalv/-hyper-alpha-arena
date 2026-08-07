import React, { useState } from 'react'
import type { Position } from '../../api/types'
import { PositionCard } from './PositionCard'
import { BottomSheet } from '../ui/BottomSheet'
import { TouchButton } from '../ui/TouchButton'

interface PositionListProps {
  positions: Position[]
  onClosePosition?: (symbol: string) => void
  onAdjustTPSL?: (position: Position) => void
}

export const PositionList: React.FC<PositionListProps> = ({ positions, onClosePosition, onAdjustTPSL }) => {
  const [selectedPosition, setSelectedPosition] = useState<Position | null>(null)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const [closePosition, setClosePosition] = useState<Position | null>(null)

  const handleClose = (pos: Position) => {
    setClosePosition(pos)
    setShowCloseConfirm(true)
  }

  const confirmClose = () => {
    if (closePosition) {
      onClosePosition?.(closePosition.symbol)
      setShowCloseConfirm(false)
      setClosePosition(null)
    }
  }

  if (positions.length === 0) {
    return (
      <div className="mx-4 mt-3 p-6 bg-surface rounded-card border border-border text-center text-muted text-sm">
        暂无持仓
      </div>
    )
  }

  return (
    <div className="mx-4 mt-3 space-y-3">
      {positions.map(pos => (
        <PositionCard
          key={pos.id}
          position={pos}
          onClose={() => handleClose(pos)}
          onAdjust={() => onAdjustTPSL?.(pos)}
        />
      ))}

      {/* Close confirm bottom sheet */}
      <BottomSheet
        open={showCloseConfirm}
        onClose={() => setShowCloseConfirm(false)}
        title="确认平仓"
      >
        {closePosition && (
          <div className="pb-6">
            <div className="text-center mb-4">
              <div className="text-xl font-bold mb-1">
                {closePosition.symbol} {closePosition.side === 'long' ? 'Long' : 'Short'}
              </div>
              <div className="text-sm text-muted">当前盈亏</div>
              <div className={`text-2xl font-bold ${closePosition.unrealized_pnl && closePosition.unrealized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                {closePosition.unrealized_pnl && closePosition.unrealized_pnl >= 0 ? '+' : ''}${closePosition.unrealized_pnl?.toFixed(2) ?? '—'}
              </div>
            </div>
            <div className="flex gap-3">
              <TouchButton variant="ghost" fullWidth onClick={() => setShowCloseConfirm(false)}>
                取消
              </TouchButton>
              <TouchButton variant="danger" fullWidth onClick={confirmClose}>
                确认平仓
              </TouchButton>
            </div>
          </div>
        )}
      </BottomSheet>
    </div>
  )
}
