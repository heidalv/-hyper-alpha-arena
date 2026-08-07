/**
 * useDebounce Hook
 *
 * PERFORMANCE OPTIMIZATION:
 * - Delays function execution until after wait milliseconds have elapsed
 * - Prevents excessive function calls (e.g., search input, API requests)
 * - Automatically cleans up pending debounced calls on unmount
 *
 * Common use cases:
 * - Search input (wait for user to stop typing)
 * - Window resize events
 * - Scroll events
 * - Auto-save functionality
 *
 * Usage:
 * ```tsx
 * const debouncedSearch = useDebounce(searchFunction, 500)
 *
 * <input onChange={(e) => debouncedSearch(e.target.value)} />
 * ```
 */

import { useEffect, useRef } from 'react'
import { useCallback, useState } from 'react'

/**
 * Returns a debounced version of the provided function
 *
 * @param callback - Function to debounce
 * @param delay - Delay in milliseconds (default: 500ms)
 * @returns Debounced function
 */
export function useDebounce<T extends (...args: any[]) => any>(
  callback: T,
  delay: number = 500
): T {
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)

  const debouncedCallback = useCallback(
    (...args: Parameters<T>) => {
      // Clear previous timeout
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }

      // Set new timeout
      timeoutRef.current = setTimeout(() => {
        callback(...args)
      }, delay)
    },
    [callback, delay]
  )

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  return debouncedCallback as T
}

/**
 * Returns a debounced value that updates after delay
 *
 * @param value - Value to debounce
 * @param delay - Delay in milliseconds (default: 500ms)
 * @returns Debounced value
 */
export function useDebouncedValue<T>(value: T, delay: number = 500): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const timeout = setTimeout(() => {
      setDebouncedValue(value)
    }, delay)

    return () => {
      clearTimeout(timeout)
    }
  }, [value, delay])

  return debouncedValue
}

/**
 * useThrottle Hook
 *
 * PERFORMANCE OPTIMIZATION:
 * - Ensures function is called at most once per wait milliseconds
 * - Useful for high-frequency events like scroll, resize, mousemove
 *
 * Usage:
 * ```tsx
 * const throttledScroll = useThrottle(handleScroll, 100)
 *
 * <div onScroll={throttledScroll} />
 * ```
 */

/**
 * Returns a throttled version of the provided function
 *
 * @param callback - Function to throttle
 * @param delay - Minimum time between calls in ms (default: 100ms)
 * @returns Throttled function
 */
export function useThrottle<T extends (...args: any[]) => any>(
  callback: T,
  delay: number = 100
): T {
  const lastRunRef = useRef<number>(Date.now())
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)

  const throttledCallback = useCallback(
    (...args: Parameters<T>) => {
      const now = Date.now()
      const timeSinceLastRun = now - lastRunRef.current

      if (timeSinceLastRun >= delay) {
        // Enough time has passed, run immediately
        lastRunRef.current = now
        callback(...args)
      } else {
        // Clear any pending timeout
        if (timeoutRef.current) {
          clearTimeout(timeoutRef.current)
        }

        // Schedule run for remaining time
        const remainingTime = delay - timeSinceLastRun
        timeoutRef.current = setTimeout(() => {
          lastRunRef.current = Date.now()
          callback(...args)
        }, remainingTime)
      }
    },
    [callback, delay]
  )

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  return throttledCallback as T
}
