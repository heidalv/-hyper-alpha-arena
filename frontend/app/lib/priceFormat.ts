/**
 * Price formatting utilities for crypto trading
 * Provides intelligent precision based on price magnitude and symbol
 */

/**
 * Major cryptocurrencies that typically have 2 decimal places
 */
const MAJOR_CRYPTO_SYMBOLS = new Set([
  'BTC', 'ETH', 'BNB', 'SOL'
])

/**
 * Format price with appropriate precision based on value and symbol
 *
 * Rules:
 * - BTC, ETH, SOL, BNB: 2 decimal places
 * - Price >= $100: 2 decimal places
 * - Price >= $1: 4 decimal places
 * - Price >= $0.01: 6 decimal places
 * - Price < $0.01: 8 decimal places
 * - Use scientific notation for very small numbers (< 0.000001)
 *
 * @param price - The price value to format
 * @param symbol - Optional symbol for symbol-specific formatting
 * @returns Formatted price string
 */
export function formatPrice(price: number | string | null | undefined, symbol?: string): string {
  // Handle null/undefined
  if (price === null || price === undefined) {
    return '-'
  }

  // Convert to number if string
  const numPrice = typeof price === 'string' ? parseFloat(price) : price

  // Handle invalid numbers
  if (isNaN(numPrice)) {
    return '-'
  }

  // Handle zero
  if (numPrice === 0) {
    return '0.00'
  }

  // Check if it's a major crypto with standard 2 decimal places
  const upperSymbol = symbol?.toUpperCase()
  if (upperSymbol && MAJOR_CRYPTO_SYMBOLS.has(upperSymbol)) {
    return numPrice.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })
  }

  // For other symbols, determine precision based on price magnitude
  const absPrice = Math.abs(numPrice)

  // Very small price - use scientific notation or high precision
  if (absPrice < 0.000001) {
    // Use scientific notation for extremely small numbers
    return numPrice.toExponential(2)
  }

  // Small price (< 1 cent) - 8 decimal places
  if (absPrice < 0.01) {
    return numPrice.toLocaleString('en-US', {
      minimumFractionDigits: absPrice < 0.0001 ? 8 : 6,
      maximumFractionDigits: absPrice < 0.0001 ? 8 : 6
    })
  }

  // Medium price (1 cent to $1) - 6 decimal places
  if (absPrice < 1) {
    return numPrice.toLocaleString('en-US', {
      minimumFractionDigits: 4,
      maximumFractionDigits: 6
    })
  }

  // Price $1 to $10 - 4 decimal places（如 LINK、UNI、DOGE 等）
  if (absPrice < 10) {
    return numPrice.toLocaleString('en-US', {
      minimumFractionDigits: 4,
      maximumFractionDigits: 4
    })
  }

  // Price $10 to $100 - 2 decimal places
  if (absPrice < 100) {
    return numPrice.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    })
  }

  // Very high price (>= $100) - 2 decimal places
  return numPrice.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })
}

/**
 * Format position size/quantity with appropriate precision
 * BTC: 4 decimals, ETH: 3 decimals, small-price tokens: 0-2 decimals
 */
export function formatSize(size: number | string | null | undefined, symbol?: string): string {
  if (size === null || size === undefined) return '-'
  const n = typeof size === 'string' ? parseFloat(size) : size
  if (isNaN(n) || n === 0) return '0'

  const abs = Math.abs(n)
  const s = symbol?.toUpperCase()

  if (s === 'BTC') return n.toFixed(4)
  if (s === 'ETH') return n.toFixed(3)
  if (s === 'BNB') return n.toFixed(2)

  if (abs >= 10000) return n.toFixed(0)
  if (abs >= 100) return n.toFixed(1)
  if (abs >= 1) return n.toFixed(2)
  if (abs >= 0.01) return n.toFixed(4)
  return n.toFixed(6)
}

/**
 * Format price with compact notation for large numbers
 * e.g., 1.2M, 3.45B
 *
 * @param price - The price value to format
 * @param symbol - Optional symbol for symbol-specific formatting
 * @returns Formatted price string with compact notation
 */
export function formatPriceCompact(price: number | string | null | undefined, symbol?: string): string {
  if (price === null || price === undefined) {
    return '-'
  }

  const numPrice = typeof price === 'string' ? parseFloat(price) : price

  if (isNaN(numPrice)) {
    return '-'
  }

  if (numPrice === 0) {
    return '0'
  }

  const absPrice = Math.abs(numPrice)

  // Use compact notation for very large numbers
  if (absPrice >= 1_000_000_000) {
    return `${(numPrice / 1_000_000_000).toFixed(2)}B`
  }

  if (absPrice >= 1_000_000) {
    return `${(numPrice / 1_000_000).toFixed(2)}M`
  }

  if (absPrice >= 1_000) {
    return `${(numPrice / 1_000).toFixed(2)}K`
  }

  // For smaller numbers, use standard price formatting
  return formatPrice(numPrice, symbol)
}

/**
 * Format percentage change with appropriate precision
 *
 * @param value - Percentage value (e.g., 5.67 for 5.67%)
 * @param decimals - Number of decimal places (default: 2)
 * @returns Formatted percentage string with % sign
 */
export function formatPercentage(value: number | string | null | undefined, decimals: number = 2): string {
  if (value === null || value === undefined) {
    return '-'
  }

  const numValue = typeof value === 'string' ? parseFloat(value) : value

  if (isNaN(numValue)) {
    return '-'
  }

  return `${numValue >= 0 ? '+' : ''}${numValue.toFixed(decimals)}%`
}

/**
 * Format volume or large numbers with appropriate precision
 *
 * @param value - Volume or large number
 * @param decimals - Number of decimal places (default: 2)
 * @returns Formatted string with K/M/B suffixes
 */
export function formatVolume(value: number | string | null | undefined, decimals: number = 2): string {
  if (value === null || value === undefined) {
    return '-'
  }

  const numValue = typeof value === 'string' ? parseFloat(value) : value

  if (isNaN(numValue)) {
    return '-'
  }

  if (numValue === 0) {
    return '0'
  }

  const absValue = Math.abs(numValue)

  if (absValue >= 1_000_000_000) {
    return `${(numValue / 1_000_000_000).toFixed(decimals)}B`
  }

  if (absValue >= 1_000_000) {
    return `${(numValue / 1_000_000).toFixed(decimals)}M`
  }

  if (absValue >= 1_000) {
    return `${(numValue / 1_000).toFixed(decimals)}K`
  }

  return numValue.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals
  })
}

/**
 * Get optimal decimal places for a symbol based on typical price range
 *
 * @param symbol - Trading symbol
 * @returns Recommended number of decimal places
 */
export function getSymbolDecimals(symbol: string): number {
  const upperSymbol = symbol.toUpperCase()

  // Major cryptos - 2 decimals
  if (MAJOR_CRYPTO_SYMBOLS.has(upperSymbol)) {
    return 2
  }

  // Stablecoins and high-value tokens - 2-4 decimals
  const stablecoins = ['USDT', 'USDC', 'DAI', 'BUSD', 'TUSD', 'USDD', 'FRAX']
  if (stablecoins.includes(upperSymbol)) {
    return 2
  }

  // Mid-tier tokens - 4 decimals
  const midTier = ['LINK', 'UNI', 'AVAX', 'MATIC', 'DOT', 'ATOM', 'NEAR', 'APT', 'ARB', 'OP']
  if (midTier.includes(upperSymbol)) {
    return 4
  }

  // Default to 4 decimals for most tokens
  return 4
}

/**
 * Format price specifically for order input fields
 * Provides consistent precision for trading
 *
 * @param price - The price value
 * @param symbol - Trading symbol
 * @returns Price string with appropriate decimal places
 */
export function formatPriceForInput(price: number | string, symbol: string): string {
  const numPrice = typeof price === 'string' ? parseFloat(price) : price
  const decimals = getSymbolDecimals(symbol)
  return numPrice.toFixed(decimals)
}

/**
 * Parse price from input string to number
 * Handles various formats including user input
 *
 * @param value - Input value
 * @returns Parsed number or null if invalid
 */
export function parsePrice(value: string): number | null {
  if (!value || value.trim() === '') {
    return null
  }

  const num = parseFloat(value)
  return isNaN(num) ? null : num
}

/**
 * Format currency value (for P&L, etc.)
 * Uses $ prefix with appropriate formatting
 *
 * @param value - The value to format
 * @returns Formatted currency string
 */
export function formatCurrency(value: number | string | null | undefined): string {
  if (value === null || value === undefined) {
    return '-'
  }

  const numValue = typeof value === 'string' ? parseFloat(value) : value

  if (isNaN(numValue)) {
    return '-'
  }

  const absValue = Math.abs(numValue)

  if (absValue >= 1_000_000_000) {
    return `$${(numValue / 1_000_000_000).toFixed(2)}B`
  }

  if (absValue >= 1_000_000) {
    return `$${(numValue / 1_000_000).toFixed(2)}M`
  }

  if (absValue >= 1_000) {
    return `$${(numValue / 1_000).toFixed(2)}K`
  }

  return `$${numValue.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })}`
}

/**
 * Format percentage value
 * Similar to formatPercentage but simpler
 *
 * @param value - The value to format
 * @returns Formatted percentage string
 */
export function formatPercent(value: number | string | null | undefined, decimals: number = 2): string {
  if (value === null || value === undefined) {
    return '-'
  }

  const numValue = typeof value === 'string' ? parseFloat(value) : value

  if (isNaN(numValue)) {
    return '-'
  }

  return `${numValue >= 0 ? '+' : ''}${numValue.toFixed(decimals)}%`
}
