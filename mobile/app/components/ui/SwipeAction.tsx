import React, { useRef, useState, useCallback } from 'react'

interface SwipeActionProps {
  onAction: () => void
  actionLabel?: string
  actionColor?: string
  /** 语义化操作颜色，与 actionColor 互斥 */
  actionVariant?: 'danger' | 'warning' | 'success'
  children: React.ReactNode
  threshold?: number
}

function SwipeAction({
  onAction,
  actionLabel = '平仓',
  actionColor,
  actionVariant,
  children,
  threshold = 80,
}: SwipeActionProps) {
  const resolvedColor = actionColor || (
    actionVariant === 'warning' ? 'bg-terminal-warning'
      : actionVariant === 'success' ? 'bg-terminal-profit'
        : 'bg-terminal-loss'
  )
  const startX = useRef(0)
  const currentX = useRef(0)
  const [offsetX, setOffsetX] = useState(0)

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    startX.current = e.touches[0].clientX
  }, [])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const diff = startX.current - e.touches[0].clientX
    if (diff > 0) {
      currentX.current = Math.min(diff, threshold * 1.5)
      setOffsetX(currentX.current)
    }
  }, [threshold])

  const onTouchEnd = useCallback(() => {
    if (currentX.current >= threshold) {
      onAction()
    }
    currentX.current = 0
    setOffsetX(0)
  }, [threshold, onAction])

  return (
    <div className="relative overflow-hidden">
      {/* Action button underneath */}
      <div className={`absolute right-0 top-0 bottom-0 ${resolvedColor} flex items-center justify-center px-6 min-w-[80px]`}>
        <span className="text-white font-medium">{actionLabel}</span>
      </div>
      {/* Content */}
      <div
        style={{ transform: `translateX(-${offsetX}px)`, transition: offsetX === 0 ? 'transform 200ms ease-out' : 'none' }}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        {children}
      </div>
    </div>
  )
}

export default SwipeAction
// 具名导出别名，兼容 `import { SwipeAction }` 写法
export { SwipeAction }
