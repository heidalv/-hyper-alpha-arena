/**
 * AnimatedNumber - 数字动画组件
 * 数字变化时平滑过渡，避免整体刷新
 */
import { useEffect, useRef, useState } from 'react'

interface AnimatedNumberProps {
  value: number
  decimals?: number
  prefix?: string
  suffix?: string
  className?: string
  colorize?: boolean // 是否根据正负值着色
}

export default function AnimatedNumber({
  value,
  decimals = 2,
  prefix = '',
  suffix = '',
  className = '',
  colorize = false,
}: AnimatedNumberProps) {
  const [displayValue, setDisplayValue] = useState(value)
  const [isAnimating, setIsAnimating] = useState(false)
  const prevValueRef = useRef(value)

  useEffect(() => {
    if (prevValueRef.current === value) return

    setIsAnimating(true)
    const startValue = prevValueRef.current
    const endValue = value
    const duration = 500 // 动画时长 500ms
    const startTime = Date.now()

    const animate = () => {
      const elapsed = Date.now() - startTime
      const progress = Math.min(elapsed / duration, 1)

      // 使用缓动函数
      const easeOutQuad = (t: number) => t * (2 - t)
      const easedProgress = easeOutQuad(progress)

      const currentValue = startValue + (endValue - startValue) * easedProgress
      setDisplayValue(currentValue)

      if (progress < 1) {
        requestAnimationFrame(animate)
      } else {
        setIsAnimating(false)
        prevValueRef.current = endValue
      }
    }

    requestAnimationFrame(animate)
  }, [value])

  const formattedValue = displayValue.toFixed(decimals)
  
  // 根据正负值着色
  const colorClass = colorize
    ? value >= 0
      ? 'text-green-600'
      : 'text-red-600'
    : ''

  return (
    <span
      className={`${className} ${colorClass} ${
        isAnimating ? 'transition-opacity' : ''
      }`}
    >
      {prefix}
      {formattedValue}
      {suffix}
    </span>
  )
}
