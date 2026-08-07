/**
 * useInterval Hook
 *
 * PERFORMANCE OPTIMIZATION:
 * - Provides a consistent way to handle intervals in React
 * - Automatically cleans up intervals on unmount
 * - Handles null delay to pause interval
 * - Prevents memory leaks from forgotten intervals
 *
 * Usage:
 * ```tsx
 * useInterval(() => {
 *   fetchData()
 * }, 5000) // Run every 5 seconds
 *
 * // To pause:
 * useInterval(() => {
 *   fetchData()
 * }, null) // Paused
 * ```
 */

import { useEffect, useRef } from 'react'

/**
 * A hook to set up an interval that automatically cleans up
 *
 * @param callback - Function to run on each interval
 * @param delay - Interval delay in ms, or null to pause
 */
export function useInterval(callback: () => void, delay: number | null) {
  const savedCallback = useRef(callback)

  // Remember the latest callback if it changes
  useEffect(() => {
    savedCallback.current = callback
  }, [callback])

  // Set up the interval
  useEffect(() => {
    // Don't schedule if delay is null (paused)
    if (delay === null) {
      return
    }

    const id = setInterval(() => {
      savedCallback.current()
    }, delay)

    // Cleanup on unmount or delay change
    return () => {
      clearInterval(id)
    }
  }, [delay])
}

/**
 * A hook that handles intervals with immediate execution
 * Runs the callback immediately, then repeats every delay ms
 *
 * @param callback - Function to run
 * @param delay - Interval delay in ms, or null to pause
 */
export function useIntervalImmediate(callback: () => void, delay: number | null) {
  const savedCallback = useRef(callback)

  useEffect(() => {
    savedCallback.current = callback
  }, [callback])

  useEffect(() => {
    if (delay === null) {
      return
    }

    // Run immediately
    savedCallback.current()

    // Then schedule repeated calls
    const id = setInterval(() => {
      savedCallback.current()
    }, delay)

    return () => {
      clearInterval(id)
    }
  }, [delay])
}

/**
 * A hook for intervals that can be enabled/disabled
 *
 * @param callback - Function to run
 * @param delay - Interval delay in ms
 * @param enabled - Whether the interval is active
 */
export function useConditionalInterval(
  callback: () => void,
  delay: number,
  enabled: boolean
) {
  const savedCallback = useRef(callback)

  useEffect(() => {
    savedCallback.current = callback
  }, [callback])

  useEffect(() => {
    if (!enabled) {
      return
    }

    const id = setInterval(() => {
      savedCallback.current()
    }, delay)

    return () => {
      clearInterval(id)
    }
  }, [delay, enabled])
}
