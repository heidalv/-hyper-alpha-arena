import { useState, useCallback, useRef } from 'react'

interface UseSwipeOptions {
  onSwipeLeft?: () => void
  onSwipeRight?: () => void
  threshold?: number
}

interface UseSwipeReturn {
  touchStartX: number
  touchCurrentX: number
  isSwiping: boolean
  swipeOffset: number
  handleTouchStart: (e: React.TouchEvent) => void
  handleTouchMove: (e: React.TouchEvent) => void
  handleTouchEnd: () => void
  resetSwipe: () => void
}

export function useSwipe(options: UseSwipeOptions = {}): UseSwipeReturn {
  const { onSwipeLeft, onSwipeRight, threshold = 80 } = options
  const [touchStartX, setTouchStartX] = useState(0)
  const [touchCurrentX, setTouchCurrentX] = useState(0)
  const [isSwiping, setIsSwiping] = useState(false)
  const startXRef = useRef(0)

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    startXRef.current = e.touches[0].clientX
    setTouchStartX(startXRef.current)
    setTouchCurrentX(startXRef.current)
    setIsSwiping(true)
  }, [])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!isSwiping) return
    const currentX = e.touches[0].clientX
    setTouchCurrentX(currentX)
  }, [isSwiping])

  const handleTouchEnd = useCallback(() => {
    const offset = touchCurrentX - touchStartX
    const absOffset = Math.abs(offset)

    if (absOffset > threshold) {
      if (offset < 0 && onSwipeLeft) {
        onSwipeLeft()
      } else if (offset > 0 && onSwipeRight) {
        onSwipeRight()
      }
    }

    setIsSwiping(false)
    setTouchStartX(0)
    setTouchCurrentX(0)
  }, [touchCurrentX, touchStartX, threshold, onSwipeLeft, onSwipeRight])

  const resetSwipe = useCallback(() => {
    setIsSwiping(false)
    setTouchStartX(0)
    setTouchCurrentX(0)
  }, [])

  return {
    touchStartX,
    touchCurrentX,
    isSwiping,
    swipeOffset: Math.max(0, touchStartX - touchCurrentX),
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
    resetSwipe
  }
}
