/**
 * Price Format Utility Tests
 */
import { describe, it, expect } from 'vitest'
import { formatPrice } from '@/lib/priceFormat'

describe('formatPrice', () => {
  it('formats null as "-"', () => {
    expect(formatPrice(null)).toBe('-')
  })

  it('formats undefined as "-"', () => {
    expect(formatPrice(undefined)).toBe('-')
  })

  it('formats zero as "0.00"', () => {
    expect(formatPrice(0)).toBe('0.00')
  })

  it('formats NaN as "-"', () => {
    expect(formatPrice(NaN)).toBe('-')
  })

  it('formats string price correctly', () => {
    const result = formatPrice('65000.50', 'BTC')
    expect(result).toContain('65,000.50')
  })

  it('formats BTC with 2 decimals', () => {
    const result = formatPrice(65000.123, 'BTC')
    expect(result).toContain('65,000.12')
  })

  it('formats ETH with 2 decimals', () => {
    const result = formatPrice(3500.456, 'ETH')
    expect(result).toContain('3,500.46')
  })

  it('formats prices >= $100 with 2 decimals', () => {
    const result = formatPrice(150.789)
    expect(result).toContain('150.79')
  })

  it('handles very small prices', () => {
    const result = formatPrice(0.00001234)
    expect(result).toBeTruthy()
    expect(result).not.toBe('-')
  })
})

describe('formatPrice edge cases', () => {
  it('handles negative prices', () => {
    const result = formatPrice(-100)
    expect(result).toBeTruthy()
  })

  it('handles very large prices', () => {
    const result = formatPrice(1000000)
    expect(result).toBeTruthy()
  })

  it('handles SOL symbol', () => {
    const result = formatPrice(150.123, 'SOL')
    expect(result).toContain('150.12')
  })

  it('handles BNB symbol', () => {
    const result = formatPrice(600.456, 'BNB')
    expect(result).toContain('600.46')
  })
})
