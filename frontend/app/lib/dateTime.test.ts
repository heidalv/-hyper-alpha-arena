/**
 * DateTime Utility Tests
 */
import { describe, it, expect } from 'vitest'
import {
  formatDateTime,
  formatDate,
  formatTime,
  formatRelativeTime,
  utcToLocalTimestamp,
  formatChartLocalTime,
  formatChartTime
} from '@/lib/dateTime'

describe('formatDateTime', () => {
  it('handles valid ISO date string', () => {
    const result = formatDateTime('2026-04-15T12:00:00Z')
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })

  it('handles empty string', () => {
    const result = formatDateTime('')
    expect(result).toBe('N/A')
  })

  it('handles null', () => {
    const result = formatDateTime(null)
    expect(result).toBe('N/A')
  })

  it('handles undefined', () => {
    const result = formatDateTime(undefined)
    expect(result).toBe('N/A')
  })

  it('handles Date object', () => {
    const result = formatDateTime(new Date('2026-04-15T12:00:00Z'))
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })

  it('handles short style', () => {
    const result = formatDateTime('2026-04-15T12:00:00Z', { style: 'short' })
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })

  it('handles long style', () => {
    const result = formatDateTime('2026-04-15T12:00:00Z', { style: 'long' })
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })
})

describe('formatDate', () => {
  it('formats a valid date', () => {
    const result = formatDate('2026-04-15T12:00:00Z')
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })

  it('returns N/A for null', () => {
    expect(formatDate(null)).toBe('N/A')
  })
})

describe('formatTime', () => {
  it('formats time without seconds', () => {
    const result = formatTime('2026-04-15T12:30:45Z')
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })

  it('formats time with seconds', () => {
    const result = formatTime('2026-04-15T12:30:45Z', { showSeconds: true })
    expect(result).toBeTruthy()
    expect(result).not.toBe('N/A')
  })
})

describe('formatRelativeTime', () => {
  it('returns "just now" for recent timestamp', () => {
    const result = formatRelativeTime(new Date().toISOString())
    expect(result).toBe('just now')
  })

  it('returns N/A for null', () => {
    expect(formatRelativeTime(null)).toBe('N/A')
  })

  it('handles past timestamps', () => {
    const oneHourAgo = new Date(Date.now() - 3600000).toISOString()
    const result = formatRelativeTime(oneHourAgo)
    expect(result).toContain('hour')
  })
})

describe('utcToLocalTimestamp / formatChartTime', () => {
  it('converts UTC timestamp', () => {
    const result = utcToLocalTimestamp(1713176400)
    expect(typeof result).toBe('number')
    expect(result).not.toBeNaN()
  })

  it('formatChartTime keeps exchange UTC timestamp unshifted', () => {
    const ts = 1713176400
    expect(formatChartTime(ts)).toBe(ts)
  })

  it('formatChartLocalTime formats chart timestamps', () => {
    const result = formatChartLocalTime(1713176400, { withDate: true })
    expect(result).toBeTruthy()
  })
})
